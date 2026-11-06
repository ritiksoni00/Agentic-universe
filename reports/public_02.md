# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.375 | +59.3% | 0.246 | +35.6% | 0.523 | +11.8% | 51% | 88 (348) |
| nw_kyc | 0.459 | +22.7% | 0.291 | +24.2% | 0.591 | -2.8% | -17% | 51 (29) |
| nw_renewal | 0.430 | -0.8% | 0.265 | +1.0% | 0.543 | -2.1% | -0% | 62 (54) |
| ALL | 0.411 | +24.3% | 0.261 | +18.9% | 0.545 | +2.3% | 26% | 201 (431) |

## Changes applied by the controller

19 applied — neutral: 7, beneficial: 9, harmful: 3

- nw_kyc v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.266 → **beneficial**
- nw_renewal v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.040 didΔ=+0.000 → **beneficial**
- ks_cancel v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.023 didΔ=+0.000 → **beneficial**
- nw_kyc v4 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.018 didΔ=+0.000 → **harmful**
- ks_cancel v4 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v5 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.611 → **beneficial**
- ks_cancel v7 (controller_approved) dims=['prompt_version'] engageΔ=+0.102 didΔ=+0.000 → **beneficial**
- nw_renewal v4 (controller_approved) dims=['voice'] engageΔ=+0.037 didΔ=+0.000 → **beneficial**
- nw_kyc v5 (controller_approved) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v5 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v8 (controller_approved) dims=['welcome_message'] engageΔ=+0.024 didΔ=+0.000 → **beneficial**
- nw_kyc v6 (controller_approved) dims=['voice'] engageΔ=+0.014 didΔ=+0.000 → **beneficial**
- nw_kyc v7 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.017 → **neutral**
- nw_renewal v7 (controller_approved) dims=['voice'] engageΔ=-0.024 didΔ=+0.000 → **harmful**
- ks_cancel v9 (controller_approved) dims=['voice'] engageΔ=+0.016 didΔ=+0.000 → **beneficial**
- nw_kyc v8 (controller_approved) dims=['voice'] engageΔ=-0.026 didΔ=+0.000 → **harmful**

## Governance

```
{
  "approval_requests": 21,
  "decisions": {
    "approved": 18,
    "rejected": 3,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 3
  },
  "applied_via_approval": 17,
  "auto_applied": 2,
  "auto_apply_rejected": 0,
  "apply_conflicts": 1,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "nw_renewal": 1,
    "nw_kyc": 2,
    "ks_cancel": 2
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.34,
      "3": 0.0,
      "4": 0.0,
      "5": 0.0,
      "6": 0.0,
      "7": 0.0,
      "8": 0.0,
      "9": 0.0
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 210,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "3": 0.327,
    "4": 0.338,
    "5": 0.332,
    "6": 0.34,
    "7": 0.348,
    "8": 0.343,
    "9": 0.333
  }
}
```

Credits used: 5 · validation runs: 1 · LLM fix calls: 1