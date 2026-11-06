from controller.statsutil import choose_winner, detect_decay, wilson_lower


def test_wilson_monotonic_in_n():
    assert wilson_lower(50, 100) > wilson_lower(5, 10)


def test_choose_winner_promotes_clear_lift():
    rows = [
        {"value": "V1", "dials": 400, "connected": 200, "engaged": 100, "halluc": 1},
        {"value": "V2", "dials": 400, "connected": 200, "engaged": 140, "halluc": 1},
    ]
    w, meta = choose_winner(
        rows, primary="engagement_rate", guardrail="pickup_rate", baseline_value="V1", min_n=50
    )
    assert w == "V2"
    assert meta["reason"] == "promote"


def test_choose_winner_guardrail():
    rows = [
        {"value": "W1", "dials": 500, "connected": 200, "engaged": 100, "halluc": 0},
        # Higher engagement but much worse pickup
        {"value": "W2", "dials": 500, "connected": 100, "engaged": 80, "halluc": 0},
    ]
    w, meta = choose_winner(
        rows, primary="engagement_rate", guardrail="pickup_rate", baseline_value="W1", min_n=50
    )
    assert w is None
    assert meta["reason"] == "guardrail_fail"


def test_decay_detection():
    recent = [
        {"value": "W3", "dials": 300, "connected": 150, "engaged": 60, "halluc": 0},
        {"value": "W1", "dials": 300, "connected": 150, "engaged": 90, "halluc": 0},
    ]
    assert detect_decay(recent, "W3", "engagement_rate", min_n=80, drop=0.03)
