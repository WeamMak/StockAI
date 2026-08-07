# Odoo 19 JSON-2 contract investigation

**Investigation date:** 2026-08-07

**Task:** T10

**Status:** **Stop condition triggered; selected resolution pending revised-document approval**

This note separates facts verified in official Odoo documentation/source from
facts that still require an executable probe against the pinned database. It
does not invent replacement behavior for missing Odoo capabilities.

## 1. Decision summary

The Purchase, Inventory, Contacts, supplier-pricelist, receipt, return, and
standard purchase-order operations needed by the project exist in Odoo 19
Community source. Two approved assumptions do not hold as written:

1. **Odoo 19 Community does not contain the standard analytic-budget
   implementation.** The pinned image contains the Community `account` and
   `analytic` modules but no `account_budget` add-on or budget model. Odoo's
   official [edition comparison](https://www.odoo.com/page/editions) places
   comprehensive accounting, including budgets, in Enterprise, while the
   [Community 19.0 source tree](https://github.com/odoo/odoo/tree/19.0/addons)
   contains no `account_budget` directory. This conflicts with the approved
   decision that Odoo 19 Community analytic budgets are the budget system of
   record.
2. **A revision check followed by a PO action is not atomic through standard
   JSON-2 calls.** Odoo documents that every JSON-2 call has its own SQL
   transaction. Reading `write_date`, checking it in MCP, and then calling
   `button_confirm` or `button_cancel` leaves a race in which another write can
   occur between calls. Standard PO actions accept no expected revision.
   Therefore the approved revision-bound confirmation/cancellation guarantee
   cannot be implemented safely by composing standard JSON-2 calls.

These findings met T10's stop condition. The user selected Odoo 19 Community
plus one project add-on for the budget model and atomic PO methods, with a
one-time ORM bootstrap Job, on 2026-08-07. Revised `docs/spec.md` and
`docs/plan.md` now define that direction, but affected implementation remains
stopped until the exact documents receive the required user and course-staff
approval.

The selected extension contracts are:

- model `stockai.procurement.budget` for company/category/analytic-account
  monthly budgets;
- `purchase.order.action_stockai_update_draft(expected, changes)`;
- `purchase.order.action_stockai_cancel_draft(expected)`;
- `purchase.order.action_stockai_confirm(expected)`; and
- a finite Odoo ORM bootstrap Job for the initial integration user/key.

The stop condition was decisive before a clean database contract suite was
created. Therefore `compose.odoo.yaml`, the executable probe, and contract tests
remain intentionally unimplemented until the revised contract is approved.

## 2. Runtime under investigation

The local probe targets these immutable official images:

- `odoo:19.0-20260803@sha256:4872f23288454b724fd2d26c176a418276c2b3552e9aa752f9396b59d864b3a0`
- `postgres@sha256:e8db9bd3e9e1751eb639fb17be53cc6d1b62a322adf75b99e791767a7a16ce69`

The official-images metadata lists `19.0-20260803`, `19.0`, `19`, and `latest`
for the same build; the moving tags must not be used as the reproducibility
contract. See the [official image manifest](https://github.com/docker-library/official-images/blob/master/library/odoo)
and Odoo's [19.0 Dockerfile](https://github.com/odoo/docker/blob/master/19.0/Dockerfile).

Direct inspection of the pulled Odoo image produced this sanitized result:

```text
purchase: present
stock: present
contacts: present
account: present
analytic: present
account_budget: absent
```

The image's `account/models/res_config_settings.py` contains a
`module_account_budget` installation switch, but the referenced add-on itself
is absent. This is evidence of a missing optional capability, not evidence that
Community supplies a hidden budget model.

## 3. Documented JSON-2 wire contract

The authoritative protocol reference is Odoo's
[External JSON-2 API](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html).

| Item | Verified documentation claim |
|---|---|
| Endpoint | `POST /json/2/<model>/<method>` |
| Authentication | `Authorization: bearer <API key>` |
| Content | `Content-Type: application/json`; method arguments are named JSON members only |
| Recordset | `ids` supplies record IDs for record methods; omit it for `@api.model` methods |
| Context | Optional `context` JSON object |
| Database | `X-Odoo-Database` is required when host/dbfilter routing does not uniquely choose one database |
| Success | HTTP 200 with the JSON-serialized method result |
| Failure | HTTP 4xx/5xx with an Odoo error object; raw `debug` data must be treated as sensitive and never forwarded to users |
| Transactions | Every call is a separate SQL transaction; success commits and error rolls back |
| Discovery | Installed models, fields, methods, and signatures are database-specific; the database `/doc` page and runtime `fields_get` are authoritative |
| Authorization | Odoo ACLs, record rules, and field access apply as the API-key user |

The same documentation warns that hosted external-API access is limited to a
Custom Odoo pricing plan. That pricing statement is not evidence that a
self-managed Community database accepts JSON-2; endpoint availability in the
pinned Community image must therefore be an explicit runtime contract test.

A representative sanitized read, subject to the runtime checks below, is:

```http
POST /json/2/purchase.order/search_read
Authorization: bearer <redacted>
X-Odoo-Database: stockai_t10
Content-Type: application/json

{
  "domain": [["origin", "=", "stockai-case-demo-001"]],
  "fields": ["id", "name", "origin", "partner_ref", "state", "write_date"],
  "limit": 1
}
```

## 4. API-key creation and bootstrap constraints

The documented public method is `res.users.apikeys.generate`. It requires an
**already-valid key belonging to the same user** in both the bearer header and
the `key` argument. Programmatic key management is restricted to Settings
administrators unless `base.enable_programmatic_api_keys=True`. The generic RPC
scope is `rpc`; revocation uses `res.users.apikeys.revoke`. A generated value is
shown/returned once and cannot later be recovered. Normal user keys must expire,
and the UI/documentation limit their lifetime to at most three months.

Consequences:

- JSON-2 cannot create the first API key from an empty database.
- Key rotation can use JSON-2 only after a valid bootstrap key exists.
- Enabling a global system parameter temporarily through several JSON-2 calls
  is explicitly unsafe because those calls are not atomic.
- The Community source has a private in-process
  `res.users.apikeys._generate` method used by Odoo's own UI. A privileged Odoo
  bootstrap process could call it as the intended integration user without an
  existing API key, but a private method is not a stable external contract and
  this path still requires a runtime bootstrap test in T11A.

Sources: [API-key implementation](https://github.com/odoo/odoo/blob/19.0/odoo/addons/base/models/res_users.py)
and [JSON-2 API-key documentation](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html#api-keys).

## 5. Community capability matrix

| Capability | Official Community evidence | Result |
|---|---|---|
| Purchase orders | [`purchase`](https://github.com/odoo/odoo/tree/19.0/addons/purchase) | Source-supported; runtime install/ACL probe required |
| Inventory, reordering, receipts, returns | [`stock`](https://github.com/odoo/odoo/tree/19.0/addons/stock) | Source-supported; runtime install/ACL probe required |
| PO-to-receipt linkage | [`purchase_stock`](https://github.com/odoo/odoo/tree/19.0/addons/purchase_stock) | Source-supported; runtime behavior probe required |
| Contacts and tags | [`contacts`](https://github.com/odoo/odoo/tree/19.0/addons/contacts) and base `res.partner` | Source-supported; runtime ACL probe required |
| Supplier pricelists | [`product.supplierinfo`](https://github.com/odoo/odoo/blob/19.0/addons/product/models/product_supplierinfo.py) | Source-supported |
| Analytic plans/accounts/distribution | [`analytic`](https://github.com/odoo/odoo/tree/19.0/addons/analytic) | Source-supported |
| Standard analytic budgets | No Community `account_budget` add-on; official edition comparison lists budgets with comprehensive Enterprise accounting | **Not supported; stop condition** |
| Atomic expected-revision PO action | No standard PO action accepts `expected_write_date`; JSON-2 calls are separate transactions | **Not supported; stop condition** |

## 6. Source-verified models, fields, and methods

These are source facts for Odoo 19. They are not yet claims that the dedicated
integration user can access every field or method in the seeded database.

### 6.1 Products, reordering, and stock

| Model | Required source-verified contract |
|---|---|
| `product.product` / `product.template` | `active`, `is_storable`, `categ_id`, `purchase_ok`, `uom_id`, `uom_ids`, `seller_ids` |
| `stock.warehouse.orderpoint` | `active`, `trigger` (`auto`/`manual`), `warehouse_id`, `location_id`, `product_id`, `product_min_qty`, `product_max_qty`, `replenishment_uom_id`, `route_id`, `company_id`, `qty_on_hand`, `qty_forecast`, `qty_to_order` |
| `stock.quant` | `product_id`, `location_id`, `lot_id`, `package_id`, `owner_id`, `quantity`, `reserved_quantity`, `available_quantity`, `company_id` |
| `stock.move` | `product_id`, `product_uom_qty` (planned demand), `quantity` (processed quantity), `product_uom`, `date`, `date_deadline`, `location_id`, `location_dest_id`, `state`, `picking_id`, `reservation_date`, `origin_returned_move_id`, `returned_move_ids`, `orderpoint_id` |

`replenishment_uom_id` is the Odoo 19 **Multiple** UoM/packaging field; older
integrations that expect `qty_multiple` must not be copied. Quants are split by
location, lot, package, and owner, so a single `stock.quant` row is not a
warehouse total. The 14-day projection must aggregate only the intended
warehouse locations and interpret source/destination usage correctly.

Sources: [product models](https://github.com/odoo/odoo/blob/19.0/addons/product/models/product_template.py),
[stock product extension](https://github.com/odoo/odoo/blob/19.0/addons/stock/models/product.py),
[reordering rules](https://github.com/odoo/odoo/blob/19.0/addons/stock/models/stock_orderpoint.py),
[quants](https://github.com/odoo/odoo/blob/19.0/addons/stock/models/stock_quant.py),
and [moves](https://github.com/odoo/odoo/blob/19.0/addons/stock/models/stock_move.py).

### 6.2 Contacts and supplier offers

| Model | Required source-verified contract |
|---|---|
| `res.partner` | `name`, `ref`, `active`, `is_company`, `parent_id`, `company_id`, `category_id`, address fields, `email`, `phone`, `vat`; `supplier_rank` and `property_supplier_payment_term_id` when `account` is installed |
| `res.partner.category` | `name`, `active` |
| `product.supplierinfo` | `partner_id`, `product_tmpl_id`, optional `product_id`, `product_uom_id`, `min_qty`, `price`, `discount`, `currency_id`, `date_start`, `date_end`, `company_id`, `delay`, `sequence` |

The approved/blocked vendor labels map to records in `res.partner.category` and
their IDs in `res.partner.category_id`. Offer selection must account for
template/variant, company, dates, quantity, UoM, currency, discount, and
sequence; choosing the smallest raw `price` is not equivalent to Odoo's seller
selection behavior.

Sources: [base contacts](https://github.com/odoo/odoo/blob/19.0/odoo/addons/base/models/res_partner.py),
[account contact extension](https://github.com/odoo/odoo/blob/19.0/addons/account/models/partner.py),
and [supplier pricelists](https://github.com/odoo/odoo/blob/19.0/addons/product/models/product_supplierinfo.py).

### 6.3 Purchase orders and standard actions

| Model | Required source-verified contract |
|---|---|
| `purchase.order` | `name`, `origin`, `partner_ref`, `partner_id`, `date_order`, `date_approve`, `date_planned`, `state`, `order_line`, `currency_id`, `payment_term_id`, `company_id`, `amount_untaxed`, `amount_tax`, `amount_total` |
| `purchase.order.line` | `order_id`, `product_id`, `product_qty`, `product_uom_id`, `date_planned`, `price_unit`, `discount`, `tax_ids`, `analytic_distribution`, `qty_received`, `qty_invoiced`, `qty_to_invoice` |
| `purchase_stock` extensions | PO `picking_ids`, `picking_type_id`, `receipt_status`, `effective_date`; PO line `move_ids`; stock move `purchase_line_id` |

`origin` is the standard source-document field suitable for the stable case ID.
`partner_ref` is the vendor's reference and must not be repurposed for internal
idempotency.

`purchase.order` states are `draft`, `sent`, `to approve`, `purchase`, and
`cancel`. Public business methods are:

- `button_confirm` — confirms when approval policy allows, otherwise moves to
  `to approve`;
- `button_approve` — approves an allowed order;
- `button_draft` — resets to draft;
- `button_cancel` — rejects locked orders and orders with non-draft/non-cancelled
  vendor bills before cancelling.

The integration must call business methods rather than directly writing
`state`. The `purchase_stock` override creates receipt pickings on approval and
adds stock-aware cancellation behavior.

Sources: [purchase orders](https://github.com/odoo/odoo/blob/19.0/addons/purchase/models/purchase_order.py),
[purchase lines](https://github.com/odoo/odoo/blob/19.0/addons/purchase/models/purchase_order_line.py),
[purchase-stock PO extension](https://github.com/odoo/odoo/blob/19.0/addons/purchase_stock/models/purchase_order.py),
[purchase-stock PO-line extension](https://github.com/odoo/odoo/blob/19.0/addons/purchase_stock/models/purchase_order_line.py),
and [purchase-stock move extension](https://github.com/odoo/odoo/blob/19.0/addons/purchase_stock/models/stock_move.py).

A representative action call is expected to have this shape, but its exact
return value and permissions remain runtime-probe items:

```http
POST /json/2/purchase.order/button_confirm
Authorization: bearer <redacted>
X-Odoo-Database: stockai_t10
Content-Type: application/json

{"ids": [42]}
```

### 6.4 Receipts and returns

| Model | Required source-verified contract |
|---|---|
| `stock.picking` | `name`, `origin`, `state`, `picking_type_id`, `picking_type_code`, `scheduled_date`, `date_done`, `location_id`, `location_dest_id`, `move_ids`, `move_line_ids`, `backorder_id`, `return_id`; actions `action_confirm`, `action_assign`, `action_cancel`, `button_validate` |
| `stock.return.picking` | Transient wizard with `picking_id`, `product_return_moves`; public `action_create_returns`, `action_create_returns_all` |
| `stock.return.picking.line` | `product_id`, `quantity`, `uom_id`, `move_id`, `wizard_id` |

Picking states are `draft`, `waiting`, `confirmed`, `assigned`, `done`, and
`cancel`. In Odoo 19, processed quantities are `stock.move.quantity` and
`stock.move.line.quantity`; older `quantity_done` examples are not this
contract. Return moves link back through `origin_returned_move_id`, and return
pickings link through `return_id`.

`button_validate` can raise validation errors or return a wizard/action rather
than completing unconditionally. Return creation depends on transient defaults
and context. Both flows require executable probes; no fixed multi-call sequence
is asserted here.

Sources: [pickings](https://github.com/odoo/odoo/blob/19.0/addons/stock/models/stock_picking.py),
[moves](https://github.com/odoo/odoo/blob/19.0/addons/stock/models/stock_move.py),
[move lines](https://github.com/odoo/odoo/blob/19.0/addons/stock/models/stock_move_line.py),
and [return wizard](https://github.com/odoo/odoo/blob/19.0/addons/stock/wizard/stock_picking_return.py).

### 6.5 Analytics and the missing budget contract

Community source provides:

- `account.analytic.plan`;
- `account.analytic.account`: `name`, `code`, `active`, `plan_id`,
  `company_id`, `partner_id`, `balance`, `debit`, `credit`;
- `account.analytic.line`: `name`, `date`, `amount`, `unit_amount`,
  `product_uom_id`, `partner_id`, `company_id`, `currency_id`;
- `analytic_distribution`, a stored JSON field inherited by
  `purchase.order.line`.

Odoo's [budget documentation](https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting/budget.html)
describes analytic budgets and committed purchases, but those user-facing facts
do not establish Community model names. The pinned Community image has no
standard budget model to probe. No budget payload or model name is therefore
claimed.

Sources: [analytic accounts](https://github.com/odoo/odoo/blob/19.0/addons/analytic/models/analytic_account.py),
[analytic lines](https://github.com/odoo/odoo/blob/19.0/addons/analytic/models/analytic_line.py),
and [analytic distribution](https://github.com/odoo/odoo/blob/19.0/addons/analytic/models/analytic_mixin.py).

## 7. Permissions contract

JSON-2 adds no authorization bypass. Relevant official ACL defaults include:

- `purchase.group_purchase_user`: full CRUD ACL on `purchase.order` and
  `purchase.order.line`;
- `stock.group_stock_user`: full CRUD on `stock.picking`, create/read/write but
  no unlink on `stock.move`, read-only on `stock.warehouse.orderpoint`;
- `stock.group_stock_manager`: full CRUD on orderpoints and stock moves;
- ordinary internal users: read-only `product.product`, `product.template`, and
  `product.supplierinfo`;
- `purchase.group_purchase_manager` or product managers: write access to
  supplier information;
- multi-company record rules restrict purchase and stock records to allowed
  companies.

The standard Purchase User role is broader than MCP's desired PO-only write
surface. The approved mitigation remains a dedicated integration user plus a
strict MCP operation allowlist; the residual breadth must be documented. Exact
installed ACLs, group implications, record rules, field restrictions, and
single-company visibility must be asserted by the runtime contract suite.

Sources: [purchase ACLs](https://github.com/odoo/odoo/blob/19.0/addons/purchase/security/ir.model.access.csv),
[purchase record rules](https://github.com/odoo/odoo/blob/19.0/addons/purchase/security/purchase_security.xml),
[stock ACLs](https://github.com/odoo/odoo/blob/19.0/addons/stock/security/ir.model.access.csv),
[stock record rules](https://github.com/odoo/odoo/blob/19.0/addons/stock/security/stock_security.xml),
and [product ACLs](https://github.com/odoo/odoo/blob/19.0/addons/product/security/ir.model.access.csv).

## 8. Revision detection

Odoo's [automatic access-log fields](https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html#automatic-fields)
define `write_date` as the timestamp of a record's last update. It is useful as
a human-visible/best-effort revision token and should be read with `id` and
`state`.

It is **not** a safe compare-and-swap through standard JSON-2:

```text
read write_date -> MCP compares -> another transaction writes -> button_confirm
```

The approved rule that confirmation and cancellation must match the exact
approved PO revision therefore needs one server-side transaction that checks
the expected revision and performs the business action. Selecting how to
provide that transaction is an architecture change and is deliberately left
unresolved.

## 9. Required executable probes after the stop decision is resolved

Official source narrows the contract but cannot replace database-specific
tests. A clean-database contract suite still must verify:

1. `/web/version` reports Odoo 19 and the compose image digests match this note.
2. `purchase`, `stock`, `purchase_stock`, `product`, `contacts`, `account`, and
   `analytic` are installed; the budget decision is reflected in installed
   modules and models.
3. Correct and incorrect `X-Odoo-Database` selection, missing/wrong bearer
   behavior, `res.users/context_get`, and safe error sanitization.
4. `fields_get` types and `/doc` method signatures for every field/method listed
   above.
5. Integration-user ACL and record-rule behavior for every read and PO action,
   including denial of vendor, reorder-rule, budget, user, and unrelated stock
   mutations.
6. JSON serialization for nested `purchase.order.create` line commands, the
   resulting `origin`, totals, `write_date`, state, and receipt linkage.
7. `button_confirm` outcomes under the selected PO approval configuration and
   `button_cancel` rejection rules.
8. On-hand/reserved aggregation, dated incoming/outgoing moves, open PO
   coverage, UoM/packaging rounding, supplier validity/discount/currency, and
   multi-company context.
9. Completed on-time and late receipts, processed quantities, partial/backorder
   behavior, and linked returns through both move and picking relationships.
10. First-key bootstrap, secret persistence without key logging, rotation,
    revocation, expiry, and idempotent rerun behavior.
11. The approved replacement for atomic expected-revision confirmation and
    cancellation, including a concurrent-write failure test.

Until those tests pass, source-supported items in this document remain design
inputs rather than claims of a verified end-to-end Odoo contract.
