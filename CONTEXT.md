# Ubiquitous Language

## Data source

A **Data Source** is a user-confirmed, versioned connection to one business
dataset. It has a stable `source_id`; its external Base token is only a
physical connector detail and is not its business identity.

## Product

A **Product** is the canonical description of a sellable item within one Data
Source. `ProductKey` is the pair `source_id + product_id`, so identical product
IDs in different data sources never identify the same Product.

## Marketing task

A **Marketing Task** is a request to generate a marketing image for one
Product. `TaskKey` is the pair `source_id + task_id`. A task refers to its
Product through `product_id`; it is not identified by product ID alone.

## Prepared task

A **Prepared Task** is the immutable hand-off from the business semantics
layer to the compliance and rendering pipeline. It contains the canonical task,
canonical product, the source configuration revision, and a Source Receipt.

## Source receipt

A **Source Receipt** records the physical Base table and record IDs used to
read a Prepared Task. It is the only locator used later for precise writeback.

## Discovery and confirmation

**Discovery** is a read-only inspection of a Base's tables, fields and small
record samples. It produces candidates, never a runnable source. **Confirmation**
is the user-selected mapping compiled into a DRAFT Data Source revision after a
read-only sample validation. Activation is a separate explicit operation.

An immutable **DRAFT Revision** becomes runnable only after its successful
dry-run evidence is recorded and an explicit compare-and-set activation moves
the active pointer. The revision JSON remains DRAFT forever; `get_active`
returns its effective ACTIVE runtime view, so historical evidence is never
rewritten.

## Configured runtime

The **Configured Runtime** is the sole consumer of activated source
configurations. Its interface is `prepare_task(source_id, task_id)`. It returns
a Prepared Task only after exact task/product lookup, field-ID schema checking,
strict value conversion, and a source receipt have completed.

## Configured orchestration

**Configured Orchestration** runs one Prepared Task through the existing
compliance, semantic review and rendering workflow, then uploads and writes back
only through the receipt's task record and the active configuration's output
fields. Its delivery idempotency key combines the semantic input fingerprint
with rules and template versions.

## Product reference

A task's **Product Reference** maps its task-side `product_id` to a Product.
It can be a direct text/number value or a linked-record cell. `AUTO` preserves
both choices until the confirmed source field type resolves the strategy.
