# Grade report — public_01_dev

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.365 | +6.8% | 0.244 | -0.4% | 0.524 | +6.4% | 28% | 60 (57) |
| nw_kyc | 0.475 | +29.7% | 0.299 | +23.7% | 0.611 | +2.3% | 12% | 23 (21) |
| nw_renewal | 0.441 | +5.9% | 0.263 | -0.3% | 0.574 | +6.1% | 20% | 53 (54) |
| ALL | 0.414 | +11.2% | 0.261 | +4.1% | 0.561 | +5.9% | 21% | 136 (132) |

## Changes applied by the controller

18 applied — neutral: 8, beneficial: 8, harmful: 2

- nw_renewal v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v2 (controller_approved) dims=['cohorts'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.068 didΔ=+0.000 → **beneficial**
- nw_kyc v3 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.335 → **beneficial**
- ks_cancel v3 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=-0.001 didΔ=+0.000 → **neutral**
- nw_renewal v4 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v4 (controller_auto) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_kyc v4 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.115 → **beneficial**
- nw_renewal v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.058 didΔ=+0.000 → **beneficial**
- ks_cancel v5 (controller_approved) dims=['welcome_message'] engageΔ=+0.043 didΔ=+0.000 → **beneficial**
- nw_kyc v5 (controller_approved) dims=['welcome_message', 'voice'] engageΔ=+0.074 didΔ=+0.000 → **beneficial**
- nw_renewal v6 (controller_approved) dims=['welcome_message'] engageΔ=-0.097 didΔ=+0.000 → **harmful**
- ks_cancel v6 (controller_approved) dims=['voice'] engageΔ=+0.049 didΔ=+0.000 → **beneficial**
- nw_kyc v6 (controller_approved) dims=['schedule'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- nw_renewal v7 (controller_approved) dims=['voice'] engageΔ=+0.000 didΔ=+0.000 → **neutral**
- ks_cancel v7 (controller_approved) dims=['caller_id'] engageΔ=+0.000 didΔ=+0.075 → **beneficial**
- nw_kyc v7 (controller_approved) dims=['welcome_message'] engageΔ=-0.022 didΔ=+0.000 → **harmful**

## Governance

```
{
  "approval_requests": 20,
  "decisions": {
    "rejected": 4,
    "approved": 16,
    "expired": 0
  },
  "rejection_reasons": {
    "approver declined (judgement call)": 4
  },
  "applied_via_approval": 16,
  "auto_applied": 2,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
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
[]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{
  "nw_renewal:welcome_message=W3": {
    "4": 0.092,
    "5": 0.1,
    "6": 0.109,
    "7": 0.102
  }
}
```

Credits used: 0 · validation runs: 0 · LLM fix calls: 0