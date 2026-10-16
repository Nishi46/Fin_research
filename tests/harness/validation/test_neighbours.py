from harness.validation.stability import neighbour_points

FRACTIONS = (0.10, 0.20)


def values(points: list[dict[str, int | float]], key: str) -> list[int | float]:
    return [p[key] for p in points]


def test_one_parameter_varies_at_a_time_with_the_others_fixed() -> None:
    points = neighbour_points({"lookback": 20, "top_n": 5}, FRACTIONS)
    assert all(p["top_n"] == 5 for p in points if p["lookback"] != 20)
    assert all(p["lookback"] == 20 for p in points if p["top_n"] != 5)
    assert sorted(values([p for p in points if p["top_n"] == 5], "lookback")) == [16, 18, 22, 24]
    assert sorted(values([p for p in points if p["lookback"] == 20], "top_n")) == [4, 6]


def test_integers_stay_integers_and_move_at_least_one_step() -> None:
    points = neighbour_points({"n": 3}, FRACTIONS)  # 3*0.9 = 2.7 -> 3 would not move
    assert all(isinstance(p["n"], int) for p in points)
    assert sorted(values(points, "n")) == [2, 4]  # 0.8 -> 2, 1.2 -> 4 (after forcing a step)
    assert 3 not in values(points, "n")


def test_floats_scale_exactly() -> None:
    points = neighbour_points({"threshold": 0.5}, FRACTIONS)
    assert sorted(values(points, "threshold")) == [0.4, 0.45, 0.55, 0.6]


def test_non_positive_values_and_duplicates_are_dropped() -> None:
    points = neighbour_points({"n": 1}, FRACTIONS)
    assert values(points, "n") == [2]  # 0 is not positive; both downs and both ups collapse
    assert neighbour_points({"zero": 0}, FRACTIONS) == []


def test_bool_and_non_numeric_parameters_are_not_varied() -> None:
    points = neighbour_points({"flag": True, "n": 10}, FRACTIONS)
    assert all(p["flag"] is True for p in points)
    assert sorted(values(points, "n")) == [8, 9, 11, 12]


def test_the_candidate_itself_is_not_among_the_neighbours() -> None:
    params = {"a": 10, "b": 7}
    assert params not in neighbour_points(params, FRACTIONS)


def test_no_numeric_parameters_gives_no_neighbours() -> None:
    assert neighbour_points({}, FRACTIONS) == []
