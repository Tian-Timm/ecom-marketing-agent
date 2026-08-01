from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from src.business_semantics import (
    CandidateConfidence,
    ConfiguredSourceRuntime,
    ConfiguredSourceOrchestrator,
    ConnectorConfig,
    DataSourceConfig,
    FieldMapping,
    LocalFileSourceConfigRepository,
    MappingSelection,
    PreparedTask,
    ProductReferenceStrategy,
    SourceConfigRevisionConflictError,
    SourceConfigUnverifiedError,
    SourceConfigValidationError,
    SourceDryRunError,
    SourceReceipt,
    SourceSchemaDriftError,
    SourceStatus,
    StandardMarketingTask,
    StandardProduct,
    TableMapping,
    compile_draft_config,
    confirm_draft_config,
    discover_source,
    ensure_schema_compatible,
    mapping_candidates,
    normalize_field_metadata,
    normalize_sample_records,
    normalize_table_metadata,
)


def mapping(field_id: str, name: str, source_type: str = "text") -> FieldMapping:
    return FieldMapping(field_id, name, source_type, required=True)


def source_config(revision: int = 1, status: SourceStatus = SourceStatus.ACTIVE) -> DataSourceConfig:
    return DataSourceConfig(
        source_id="cha_cup_cn",
        display_name="CHA CUP 国内营销 Base",
        connector=ConnectorConfig("feishu_base", "base-token", "FEISHU_PRIMARY_APP"),
        revision=revision,
        status=status,
        schema_fingerprint="schema-v1",
        tables={
            "products": TableMapping("tbl_products", "product_id", {
                "product_id": mapping("fld_product_id", "商品编码"),
                "product_name": mapping("fld_product_name", "商品名称"),
                "min_price": mapping("fld_min_price", "营销底价", "number"),
            }),
            "tasks": TableMapping("tbl_tasks", "task_id", {
                "task_id": mapping("fld_task_id", "工单编号"),
                "product_id": mapping("fld_task_product", "关联商品", "linked_record"),
                "img_type": mapping("fld_img_type", "图片类型"),
                "aspect_ratio": mapping("fld_ratio", "画布比例"),
                "main_text": mapping("fld_main_text", "主文案"),
                "promo_price": mapping("fld_promo_price", "活动价", "number"),
                "deploy_date": mapping("fld_deploy_date", "投放日期", "date"),
            }),
        },
        writeback={"status": mapping("fld_status", "审查状态", "single_select")},
    )


class FakeDiscoveryClient:
    def __init__(self, *, linked_product: bool = False) -> None:
        self.linked_product = linked_product
        self.write_calls = 0

    def list_tables(self, base_token: str):
        return {"data": {"items": [
            {"table_id": "tbl_products", "name": "商品资料"},
            {"table_id": "tbl_tasks", "name": "出图任务"},
        ]}}

    def list_fields(self, base_token: str, table_id: str):
        if table_id == "tbl_products":
            return {"data": {"items": [
                {"field_id": "p_id", "field_name": "商品编码", "type": "text"},
                {"field_id": "p_name", "field_name": "商品名称", "type": "text"},
                {"field_id": "p_min", "field_name": "营销底价", "type": "number"},
            ]}}
        product_type = "single_link" if self.linked_product else "text"
        return {"data": {"items": [
            {"field_id": "t_id", "field_name": "工单编号", "type": "text"},
            {"field_id": "t_product", "field_name": "商品编码", "type": product_type},
            {"field_id": "t_img", "field_name": "图片类型", "type": "text"},
            {"field_id": "t_ratio", "field_name": "画布比例", "type": "text"},
            {"field_id": "t_text", "field_name": "主文案", "type": "text"},
            {"field_id": "t_price", "field_name": "活动价", "type": "number"},
            {"field_id": "t_date", "field_name": "投放日期", "type": "date"},
            {"field_id": "t_status", "field_name": "审查状态", "type": "single_select"},
        ]}}

    def list_records_sample(self, base_token: str, table_id: str, limit: int):
        if table_id == "tbl_products":
            return {"data": {"items": [{"record_id": "rec_product", "fields": {
                "商品编码": "P-001", "商品名称": "CHA CUP 保温杯", "营销底价": 89,
            }}]}}
        reference = [{"record_id": "rec_product"}] if self.linked_product else "P-001"
        return {"data": {"items": [{"record_id": "rec_task", "fields": {
            "工单编号": "TASK-001", "商品编码": reference, "图片类型": "电商主图",
            "画布比例": "1:1", "主文案": "城市轻装", "活动价": 99,
            "投放日期": "2026-08-01", "审查状态": "待审查",
        }}]}}


class WritebackDiscoveryClient(FakeDiscoveryClient):
    """Discovery fixture with output fields supplied by another Base schema."""

    def list_fields(self, base_token: str, table_id: str):
        fields = list(super().list_fields(base_token, table_id)["data"]["items"])
        if table_id == "tbl_tasks":
            fields.extend([
                {"field_id": "t_issues", "field_name": "问题说明", "type": "text"},
                {"field_id": "t_image", "field_name": "生成图片", "type": "attachment"},
            ])
        return {"data": {"items": fields}}


class RuntimeGateway:
    """A read-only Feishu adapter fixture; records are keyed by current field names."""

    def __init__(self, bases: dict[str, dict], *, linked_product: bool = False) -> None:
        self.bases = bases
        self.linked_product = linked_product
        self.names = {
            "p_id": "商品编码", "p_name": "商品名称", "p_min": "营销底价", "p_image": "产品图",
            "t_id": "工单编号", "t_product": "商品编码", "t_img": "图片类型", "t_ratio": "画布比例",
            "t_text": "主文案", "t_price": "活动价", "t_date": "投放日期", "t_status": "审查状态",
        }
        self.types = {
            "p_id": "text", "p_name": "text", "p_min": "number", "p_image": "attachment",
            "t_id": "text", "t_product": "single_link" if linked_product else "text", "t_img": "text",
            "t_ratio": "text", "t_text": "text", "t_price": "number", "t_date": "date", "t_status": "single_select",
        }
        self.write_calls = 0

    def list_tables(self, base_token: str):
        return [{"table_id": "tbl_products", "name": "商品资料"}, {"table_id": "tbl_tasks", "name": "出图任务"}]

    def list_fields(self, base_token: str, table_id: str):
        ids = ["p_id", "p_name", "p_min", "p_image"] if table_id == "tbl_products" else [
            "t_id", "t_product", "t_img", "t_ratio", "t_text", "t_price", "t_date", "t_status"
        ]
        return [{"field_id": field_id, "field_name": self.names[field_id], "type": self.types[field_id]} for field_id in ids]

    def list_records(self, base_token: str, table_id: str):
        role = "products" if table_id == "tbl_products" else "tasks"
        return [
            {"record_id": record["record_id"], "fields": {self.names[key]: value for key, value in record["fields"].items()}}
            for record in self.bases[base_token][role]
        ]

    def list_records_sample(self, base_token: str, table_id: str, limit: int):
        return self.list_records(base_token, table_id)[:limit]


def runtime_base(product_name: str = "CHA CUP", *, product_id: str = "P-001", task_id: str = "TASK-001", linked: bool = False) -> dict:
    product_record = "rec-product"
    reference = [{"record_id": product_record}] if linked else product_id
    return {
        "products": [{"record_id": product_record, "fields": {
            "p_id": product_id, "p_name": product_name, "p_min": 89,
            "p_image": [{"file_token": f"image-{product_name}"}],
        }}],
        "tasks": [{"record_id": "rec-task", "fields": {
            "t_id": task_id, "t_product": reference, "t_img": "电商主图", "t_ratio": "1:1",
            "t_text": "城市轻装", "t_price": 99, "t_date": "2026-08-01", "t_status": "待审查",
        }}],
    }


def selection(strategy: ProductReferenceStrategy = ProductReferenceStrategy.AUTO) -> MappingSelection:
    return MappingSelection(
        product_table_id="tbl_products", task_table_id="tbl_tasks",
        product_fields={"product_id": "p_id", "product_name": "p_name", "min_price": "p_min"},
        task_fields={
            "task_id": "t_id", "product_id": "t_product", "img_type": "t_img",
            "aspect_ratio": "t_ratio", "main_text": "t_text", "promo_price": "t_price",
            "deploy_date": "t_date",
        },
        writeback_fields={"status": "t_status"},
        product_reference_strategy=strategy,
    )


def runtime_selection() -> MappingSelection:
    base = selection()
    return MappingSelection(
        base.product_table_id, base.task_table_id,
        {**base.product_fields, "product_image": "p_image"},
        base.task_fields, base.writeback_fields, base.product_reference_strategy,
    )


def activate_runtime_source(repo, gateway, source_id: str, base_token: str, *, revision: int = 1) -> None:
    snapshot = discover_source(gateway, source_id=source_id, display_name=source_id, base_token=base_token, credential_ref="FEISHU")
    confirm_draft_config(repo, snapshot, runtime_selection(), revision=revision)
    repo.activate(source_id, revision, expected_active_revision=None)


class DeliveryRuntimeGateway(RuntimeGateway):
    def __init__(self, bases: dict[str, dict]) -> None:
        super().__init__(bases)
        self.names.update({"t_issues": "问题说明", "t_processed": "处理时间", "t_hash": "输入指纹", "t_pipeline": "处理版本", "t_attachment": "生成图片"})
        self.types.update({"t_issues": "text", "t_processed": "date", "t_hash": "text", "t_pipeline": "text", "t_attachment": "attachment"})
        self.uploads: list[tuple[str, str, str]] = []
        self.updates: list[tuple[str, str, dict]] = []
        self.fail_upload = False
        self.fail_write = False

    def list_fields(self, base_token: str, table_id: str):
        fields = super().list_fields(base_token, table_id)
        if table_id == "tbl_tasks":
            fields.extend({"field_id": field_id, "field_name": self.names[field_id], "type": self.types[field_id]} for field_id in ["t_issues", "t_processed", "t_hash", "t_pipeline", "t_attachment"])
        return fields

    def upload_image_for_base(self, file_path: Path, task_id: str, base_token: str):
        if self.fail_upload:
            raise RuntimeError("upload failed")
        self.uploads.append((str(file_path), task_id, base_token))
        return {"url": None, "attachment": [{"file_token": "result-token"}]}

    def batch_update(self, base_token: str, table_id: str, updates):
        if self.fail_write:
            raise RuntimeError("write failed")
        copied = {record_id: dict(fields) for record_id, fields in updates.items()}
        self.updates.append((base_token, table_id, copied))
        reverse = {name: field_id for field_id, name in self.names.items()}
        for record in self.bases[base_token]["tasks"]:
            if record["record_id"] in copied:
                for name, value in copied[record["record_id"]].items():
                    record["fields"][reverse[name]] = value

    def download_media(self, token: str):
        return b"configured-product-material", "image/png"


def delivery_selection() -> MappingSelection:
    selected = runtime_selection()
    return MappingSelection(
        selected.product_table_id, selected.task_table_id, selected.product_fields, selected.task_fields,
        {
            "status": "t_status", "issues": "t_issues", "processed_at": "t_processed",
            "input_hash": "t_hash", "pipeline_version": "t_pipeline", "image_attachment": "t_attachment",
        }, selected.product_reference_strategy,
    )


def activate_delivery_source(repo, gateway, source_id: str, base_token: str) -> None:
    snapshot = discover_source(gateway, source_id=source_id, display_name=source_id, base_token=base_token, credential_ref="FEISHU")
    confirm_draft_config(repo, snapshot, delivery_selection(), revision=1)
    repo.activate(source_id, 1, expected_active_revision=None)


class DataSourceConfigTests(unittest.TestCase):
    def test_config_json_round_trip_writes_stable_status_string(self) -> None:
        config = source_config()
        raw = config.to_dict()
        self.assertEqual(raw["status"], "ACTIVE")
        self.assertEqual(DataSourceConfig.from_dict(raw), config)

    def test_product_and_task_keys_are_scoped_by_source(self) -> None:
        task = StandardMarketingTask("T-1", "P-1", "电商主图", "1:1", "2026-08-01", "夏日好物")
        product = StandardProduct("P-1", "CHA CUP")
        self.assertEqual(task.key_for("cha_cup_cn").value, "cha_cup_cn:T-1")
        self.assertNotEqual(product.key_for("cha_cup_cn"), product.key_for("cha_cup_us"))

    def test_config_rejects_task_input_writeback_collision(self) -> None:
        config = source_config()
        bad = DataSourceConfig(**{**config.__dict__, "writeback": {"status": mapping("fld_task_id", "审查状态")}})
        with self.assertRaises(SourceConfigValidationError):
            bad.validate()

    def test_config_rejects_conflicting_physical_field_mapping(self) -> None:
        config = source_config()
        bad_tasks = TableMapping("tbl_tasks", "task_id", {
            **config.tables["tasks"].fields,
            "product_id": mapping("fld_task_id", "关联商品"),
        })
        bad = DataSourceConfig(**{**config.__dict__, "tables": {**config.tables, "tasks": bad_tasks}})
        with self.assertRaises(SourceConfigValidationError):
            bad.validate()

    def test_prepared_task_requires_matching_product_and_receipt(self) -> None:
        task = StandardMarketingTask("T-1", "P-1", "电商主图", "1:1", "2026-08-01", "夏日好物")
        product = StandardProduct("P-1", "CHA CUP")
        receipt = SourceReceipt("cha_cup_cn", 1, "products", "rec_p", "tasks", "rec_t")
        prepared = PreparedTask("cha_cup_cn", 1, task, product, receipt, "schema-v1")
        self.assertEqual(prepared.product_key.value, "cha_cup_cn:P-1")


class DiscoveryAndConfirmationTests(unittest.TestCase):
    def test_feishu_and_cli_metadata_shapes_are_normalized(self) -> None:
        tables = normalize_table_metadata({"data": {"tables": [{"tableId": "tbl_1", "tableName": "任务"}]}})
        fields = normalize_field_metadata({"data": {"fields": [{"fieldId": "fld_1", "fieldName": "关联商品", "fieldType": "18"}]}})
        samples = normalize_sample_records([{"_record_id": "rec_1", "任务ID": "T-1"}])
        self.assertEqual((tables[0].table_id, tables[0].name), ("tbl_1", "任务"))
        self.assertEqual((fields[0].field_id, fields[0].normalized_type), ("fld_1", "linked_record"))
        self.assertEqual((samples[0].record_id, samples[0].fields["任务ID"]), ("rec_1", "T-1"))

    def test_candidates_have_deterministic_confidence_and_evidence(self) -> None:
        snapshot = discover_source(FakeDiscoveryClient(), source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        candidate = next(item for item in mapping_candidates(snapshot) if item.semantic_name == "task_id" and item.field_id == "t_id")
        self.assertEqual(candidate.confidence, CandidateConfidence.HIGH)
        self.assertIn("字段名与标准别名完全匹配", candidate.reasons)
        self.assertTrue(any("唯一率" in reason for reason in candidate.reasons))

    def test_discovery_recommends_writeback_fields_without_fixed_column_names(self) -> None:
        snapshot = discover_source(WritebackDiscoveryClient(), source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        candidates = {(item.semantic_name, item.field_id) for item in mapping_candidates(snapshot)}
        self.assertIn(("issues", "t_issues"), candidates)
        self.assertIn(("image_attachment", "t_image"), candidates)

    def test_text_and_linked_record_product_references_both_compile(self) -> None:
        text_snapshot = discover_source(FakeDiscoveryClient(), source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        text_config = compile_draft_config(text_snapshot, selection(), revision=1)
        self.assertEqual(text_config.product_reference.strategy, ProductReferenceStrategy.AUTO)

        link_snapshot = discover_source(FakeDiscoveryClient(linked_product=True), source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        linked_config = compile_draft_config(link_snapshot, selection(), revision=1)
        self.assertEqual(linked_config.tables["tasks"].fields["product_id"].source_type, "linked_record")

    def test_field_rename_is_compatible_but_deletion_and_type_change_block(self) -> None:
        client = FakeDiscoveryClient()
        snapshot = discover_source(client, source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        config = compile_draft_config(snapshot, selection(), revision=1)

        renamed = FakeDiscoveryClient()
        original_fields = renamed.list_fields
        def renamed_fields(base_token: str, table_id: str):
            payload = original_fields(base_token, table_id)
            if table_id == "tbl_tasks":
                payload["data"]["items"][0]["field_name"] = "新工单编号"
            return payload
        renamed.list_fields = renamed_fields  # type: ignore[method-assign]
        renamed_snapshot = discover_source(renamed, source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        self.assertEqual(snapshot.schema_fingerprint, renamed_snapshot.schema_fingerprint)
        self.assertTrue(ensure_schema_compatible(config, renamed_snapshot).is_compatible)

        changed = FakeDiscoveryClient()
        original_changed = changed.list_fields
        def changed_fields(base_token: str, table_id: str):
            payload = original_changed(base_token, table_id)
            if table_id == "tbl_tasks":
                payload["data"]["items"] = [item for item in payload["data"]["items"] if item["field_id"] != "t_text"]
                payload["data"]["items"][0]["type"] = "number"
                next(item for item in payload["data"]["items"] if item["field_id"] == "t_status")["type"] = "text"
            return payload
        changed.list_fields = changed_fields  # type: ignore[method-assign]
        changed_snapshot = discover_source(changed, source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        with self.assertRaises(SourceSchemaDriftError) as failure:
            ensure_schema_compatible(config, changed_snapshot)
        codes = {issue["code"] for issue in failure.exception.details["issues"]}
        self.assertEqual(codes, {"FIELD_MISSING", "FIELD_TYPE_INCOMPATIBLE"})
        self.assertTrue(any(issue["table"] == "writeback" for issue in failure.exception.details["issues"]))

    def test_confirmation_saves_only_draft_after_read_only_validation(self) -> None:
        client = FakeDiscoveryClient()
        snapshot = discover_source(client, source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalFileSourceConfigRepository(Path(directory))
            result = confirm_draft_config(repository, snapshot, selection(), revision=1)
            self.assertEqual(result.config.status, SourceStatus.DRAFT)
            self.assertEqual(repository.get("cha_cup_cn", 1).status, SourceStatus.DRAFT)
        self.assertEqual(client.write_calls, 0)

    def test_confirmation_rejects_bad_sample_without_persisting(self) -> None:
        client = FakeDiscoveryClient()
        source_records = client.list_records_sample
        def bad_records(base_token: str, table_id: str, limit: int):
            payload = source_records(base_token, table_id, limit)
            if table_id == "tbl_tasks":
                payload["data"]["items"][0]["fields"]["活动价"] = "无"
            return payload
        client.list_records_sample = bad_records  # type: ignore[method-assign]
        snapshot = discover_source(client, source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalFileSourceConfigRepository(Path(directory))
            with self.assertRaises(SourceDryRunError):
                confirm_draft_config(repository, snapshot, selection(), revision=1)
            self.assertEqual(repository.list_source_ids(), [])


class LocalRepositoryTests(unittest.TestCase):
    def test_verified_draft_becomes_effectively_active_via_compare_and_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory))
            first = source_config(1, SourceStatus.DRAFT)
            repo.save(first)
            with self.assertRaises(SourceConfigUnverifiedError):
                repo.activate(first.source_id, 1, expected_active_revision=None)
            repo.mark_dry_run_verified(first.source_id, 1, evidence={"samples": 2})
            with self.assertRaises(SourceConfigRevisionConflictError):
                repo.mark_dry_run_verified(first.source_id, 1, evidence={"samples": 99})
            repo.activate(first.source_id, 1, expected_active_revision=None)
            active = repo.get_active(first.source_id)
            self.assertEqual((active.revision, active.status), (1, SourceStatus.ACTIVE))
            self.assertEqual(repo.get(first.source_id, 1).status, SourceStatus.DRAFT)
            with self.assertRaises(SourceConfigRevisionConflictError):
                repo.save(first, expected_active_revision=1)
            second = source_config(2, SourceStatus.DRAFT)
            repo.save(second, expected_active_revision=1)
            repo.mark_dry_run_verified(second.source_id, 2, evidence={"samples": 2})
            with self.assertRaises(SourceConfigRevisionConflictError):
                repo.activate(second.source_id, 2, expected_active_revision=None)
            repo.activate(second.source_id, 2, expected_active_revision=1)
            self.assertEqual(repo.get_active(second.source_id).revision, 2)

    def test_confirmed_draft_has_verification_proof_and_can_be_explicitly_activated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory))
            snapshot = discover_source(FakeDiscoveryClient(), source_id="cha_cup_cn", display_name="CHA CUP", base_token="token", credential_ref="FEISHU")
            confirmation = confirm_draft_config(repo, snapshot, selection(), revision=1)
            self.assertEqual(confirmation.config.status, SourceStatus.DRAFT)
            repo.activate("cha_cup_cn", 1, expected_active_revision=None)
            repo.get_active("cha_cup_cn").require_active()


class ConfiguredRuntimeTests(unittest.TestCase):
    def test_direct_value_resolves_exact_records_receipt_and_product_fingerprint(self) -> None:
        gateway = RuntimeGateway({"base-a": runtime_base("A")})
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory))
            activate_runtime_source(repo, gateway, "source_a", "base-a")
            runtime = ConfiguredSourceRuntime(repo, gateway)
            prepared = runtime.prepare_task("source_a", "TASK-001")
            self.assertEqual((prepared.task.product_id, prepared.product.product_name), ("P-001", "A"))
            self.assertEqual((prepared.receipt.task_record_id, prepared.receipt.product_record_id), ("rec-task", "rec-product"))
            original_fingerprint = prepared.input_fingerprint
            gateway.bases["base-a"]["products"][0]["fields"]["p_min"] = 90
            self.assertNotEqual(runtime.prepare_task("source_a", "TASK-001").input_fingerprint, original_fingerprint)
        self.assertEqual(gateway.write_calls, 0)

    def test_same_ids_in_different_sources_do_not_cross(self) -> None:
        gateway = RuntimeGateway({"base-a": runtime_base("A"), "base-b": runtime_base("B")})
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory))
            activate_runtime_source(repo, gateway, "source_a", "base-a")
            activate_runtime_source(repo, gateway, "source_b", "base-b")
            runtime = ConfiguredSourceRuntime(repo, gateway)
            self.assertEqual(runtime.prepare_task("source_a", "TASK-001").product.product_name, "A")
            self.assertEqual(runtime.prepare_task("source_b", "TASK-001").product.product_name, "B")

    def test_missing_duplicate_and_missing_product_id_are_blocked(self) -> None:
        gateway = RuntimeGateway({"base-a": runtime_base()})
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory))
            activate_runtime_source(repo, gateway, "source_a", "base-a")
            runtime = ConfiguredSourceRuntime(repo, gateway)
            with self.assertRaises(Exception) as missing:
                runtime.prepare_task("source_a", "UNKNOWN")
            self.assertEqual(missing.exception.code, "TASK_NOT_FOUND")
            gateway.bases["base-a"]["tasks"].append(runtime_base()["tasks"][0])
            with self.assertRaises(Exception) as duplicate:
                runtime.prepare_task("source_a", "TASK-001")
            self.assertEqual(duplicate.exception.code, "DUPLICATE_TASK_ID")
            gateway.bases["base-a"]["tasks"] = [runtime_base()["tasks"][0]]
            gateway.bases["base-a"]["tasks"][0]["fields"]["t_product"] = ""
            with self.assertRaises(Exception) as no_product_id:
                runtime.prepare_task("source_a", "TASK-001")
            self.assertEqual(no_product_id.exception.code, "VALUE_CONVERSION_FAILED")
            gateway.bases["base-a"]["tasks"][0]["fields"]["t_product"] = "P-404"
            with self.assertRaises(Exception) as no_product:
                runtime.prepare_task("source_a", "TASK-001")
            self.assertEqual(no_product.exception.code, "PRODUCT_NOT_FOUND")
            gateway.bases["base-a"]["tasks"][0]["fields"]["t_product"] = "P-001"
            duplicate_product = runtime_base()["products"][0]
            duplicate_product["record_id"] = "rec-product-2"
            gateway.bases["base-a"]["products"].append(duplicate_product)
            with self.assertRaises(Exception) as duplicate_product_error:
                runtime.prepare_task("source_a", "TASK-001")
            self.assertEqual(duplicate_product_error.exception.code, "DUPLICATE_PRODUCT_ID")

    def test_linked_record_and_schema_rename_or_drift(self) -> None:
        gateway = RuntimeGateway({"base-a": runtime_base(linked=True)}, linked_product=True)
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory))
            activate_runtime_source(repo, gateway, "source_a", "base-a")
            runtime = ConfiguredSourceRuntime(repo, gateway)
            self.assertEqual(runtime.prepare_task("source_a", "TASK-001").product.product_id, "P-001")
            gateway.bases["base-a"]["tasks"][0]["fields"]["t_product"] = {"recordId": "rec-product"}
            self.assertEqual(runtime.prepare_task("source_a", "TASK-001").product.product_id, "P-001")
            gateway.names["t_id"] = "新工单编号"
            self.assertEqual(runtime.prepare_task("source_a", "TASK-001").task.task_id, "TASK-001")
            gateway.types["p_min"] = "text"
            with self.assertRaises(Exception) as drift:
                runtime.prepare_task("source_a", "TASK-001")
            self.assertEqual(drift.exception.code, "SOURCE_SCHEMA_DRIFTED")


class ConfiguredOrchestrationTests(unittest.TestCase):
    def test_default_reviewer_is_auto_but_explicit_none_disables_it(self) -> None:
        from src.business_semantics import orchestration as module
        calls: list[object] = []
        scripts = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "simple-visual-compliance" / "scripts"
        sys.path.insert(0, str(scripts))
        import run_pipeline
        default_marker = object()
        def fake_execute(*args, semantic_reviewer=default_marker, **kwargs):
            calls.append(semantic_reviewer)
            return {"status": "BLOCKED"}
        with patch.object(run_pipeline, "execute_task", fake_execute):
            module._existing_executor({}, Path("."), source="test", reviewer=module._AUTO_REVIEWER)
            module._existing_executor({}, Path("."), source="test", reviewer=None)
        self.assertIs(calls[0], default_marker)
        self.assertIsNone(calls[1])

    def _executor(self, status: str, calls: list[dict]):
        def execute(record, output_dir, *, source, reviewer):
            calls.append(dict(record))
            if status == "PASSED":
                image = output_dir / f"{record['task_id']}_rendered.png"
                image.write_bytes(b"png")
                return {"status": "PASSED", "generated_image": image.name, "violations": [], "rules_version": "rules-1", "pipeline_version": "template-1"}
            return {"status": status, "generated_image": None, "violations": [{"message": "不通过"}], "rules_version": "rules-1", "pipeline_version": "template-1"}
        return execute

    def test_passed_uploads_source_specific_material_and_writes_exact_receipt(self) -> None:
        gateway = DeliveryRuntimeGateway({"base-a": runtime_base("A")})
        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory) / "configs")
            activate_delivery_source(repo, gateway, "source_a", "base-a")
            gateway.names["t_hash"] = "新输入指纹"
            orchestrator = ConfiguredSourceOrchestrator(repo, gateway, output_root=Path(directory) / "output", pipeline_executor=self._executor("PASSED", calls), rules_version="rules-1", template_version="template-1")
            result = orchestrator.run_task("source_a", "TASK-001")
            self.assertEqual(result["sync_status"], "COMPLETED")
            self.assertEqual(gateway.uploads[0][2], "base-a")
            self.assertEqual(gateway.updates[0][1], "tbl_tasks")
            self.assertIn("rec-task", gateway.updates[0][2])
            self.assertTrue(Path(calls[0]["product_image_path"]).exists())
            self.assertNotIn("补充要求", gateway.updates[0][2]["rec-task"])
            self.assertIn("新输入指纹", gateway.updates[0][2]["rec-task"])

    def test_blocked_dry_run_skip_force_and_versions_are_observable(self) -> None:
        gateway = DeliveryRuntimeGateway({"base-a": runtime_base("A")})
        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory) / "configs")
            activate_delivery_source(repo, gateway, "source_a", "base-a")
            root = Path(directory) / "output"
            blocked = ConfiguredSourceOrchestrator(repo, gateway, output_root=root, pipeline_executor=self._executor("BLOCKED", calls), rules_version="rules-1", template_version="template-1")
            blocked_result = blocked.run_task("source_a", "TASK-001")
            self.assertEqual(blocked_result["status"], "BLOCKED")
            self.assertEqual(gateway.uploads, [])
            blocked_skip = blocked.run_task("source_a", "TASK-001")
            self.assertEqual((blocked_skip["status"], blocked_skip["sync_status"]), ("BLOCKED", "SKIPPED_UNCHANGED"))
            self.assertEqual(blocked_skip["issues"], "不通过")
            before_updates = len(gateway.updates)
            self.assertEqual(blocked.run_task("source_a", "TASK-001", dry_run=True, force=True)["sync_status"], "DRY_RUN")
            self.assertEqual(len(gateway.updates), before_updates)
            passed = ConfiguredSourceOrchestrator(repo, gateway, output_root=root, pipeline_executor=self._executor("PASSED", calls), rules_version="rules-2", template_version="template-1")
            passed.run_task("source_a", "TASK-001")
            passed_skip = passed.run_task("source_a", "TASK-001")
            self.assertEqual((passed_skip["status"], passed_skip["sync_status"]), ("PASSED", "SKIPPED_UNCHANGED"))
            self.assertEqual(passed_skip["image_attachment"], [{"file_token": "result-token"}])
            self.assertEqual(passed.run_task("source_a", "TASK-001", force=True)["sync_status"], "COMPLETED")
            snapshot = discover_source(gateway, source_id="source_a", display_name="source_a", base_token="base-a", credential_ref="FEISHU")
            confirm_draft_config(repo, snapshot, delivery_selection(), revision=2, expected_active_revision=1)
            repo.activate("source_a", 2, expected_active_revision=1)
            self.assertEqual(passed.run_task("source_a", "TASK-001")["sync_status"], "COMPLETED")
            changed_rules = ConfiguredSourceOrchestrator(repo, gateway, output_root=root, pipeline_executor=self._executor("PASSED", calls), rules_version="rules-3", template_version="template-1")
            self.assertEqual(changed_rules.run_task("source_a", "TASK-001")["sync_status"], "COMPLETED")

    def test_same_task_id_uses_source_isolated_output_directories(self) -> None:
        gateway = DeliveryRuntimeGateway({"base-a": runtime_base("A"), "base-b": runtime_base("B")})
        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory) / "configs")
            activate_delivery_source(repo, gateway, "source_a", "base-a")
            activate_delivery_source(repo, gateway, "source_b", "base-b")
            root = Path(directory) / "output"
            orch = ConfiguredSourceOrchestrator(repo, gateway, output_root=root, pipeline_executor=self._executor("PASSED", calls), rules_version="rules-1", template_version="template-1")
            orch.run_task("source_a", "TASK-001")
            orch.run_task("source_b", "TASK-001")
            self.assertTrue((root / "source_a" / "TASK-001_rendered.png").exists())
            self.assertTrue((root / "source_b" / "TASK-001_rendered.png").exists())

    def test_upload_and_writeback_failures_are_structured(self) -> None:
        gateway = DeliveryRuntimeGateway({"base-a": runtime_base("A")})
        calls: list[dict] = []
        with tempfile.TemporaryDirectory() as directory:
            repo = LocalFileSourceConfigRepository(Path(directory) / "configs")
            activate_delivery_source(repo, gateway, "source_a", "base-a")
            orch = ConfiguredSourceOrchestrator(repo, gateway, output_root=Path(directory) / "output", pipeline_executor=self._executor("PASSED", calls), rules_version="rules-1", template_version="template-1")
            gateway.fail_upload = True
            self.assertEqual(orch.run_task("source_a", "TASK-001")["error"]["code"], "FEISHU_UPLOAD_FAILED")
            gateway.fail_upload = False
            gateway.fail_write = True
            self.assertEqual(orch.run_task("source_a", "TASK-001", force=True)["error"]["code"], "FEISHU_WRITEBACK_FAILED")


if __name__ == "__main__":
    unittest.main()
