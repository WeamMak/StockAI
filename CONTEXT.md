# Procurement

This context covers the evidence-backed replenishment case from shortage
detection through a manager-controlled fictional purchase order outcome.

## Language

**Procurement Case**:
The environment-bound record of one product's replenishment decision, evidence,
draft purchase order, approvals, and outcome.
_Avoid_: Order case, workflow item

**Evidence Reference**:
A stable pointer to a captured procurement fact used by a Procurement Case.
_Avoid_: Proof, raw vendor data

**Revision**:
The positive, increasing version of a Procurement Case or draft purchase order
to which a decision is bound.
_Avoid_: Version number when referring to preference-profile versions

**Manager Approval**:
A decision bound to the exact environment, Procurement Case, draft purchase
order, Revision, vendor, quantity, amount, and evidence.
_Avoid_: Confirmation, sign-off

**Manager Change Request**:
A bounded case-specific instruction that invalidates the current recommendation
and requires evidence and policy to be recomputed.
_Avoid_: Preference change, prompt

**Manual Review**:
A safe case state used when automation lacks valid evidence, policy, or a
reliable dependency result and must not create a draft.
_Avoid_: Failure, fallback approval

**Reconciliation Required**:
A safe case state used when the outcome of an Odoo write is ambiguous and must
be established before any repeat write.
_Avoid_: Retry, unknown error
