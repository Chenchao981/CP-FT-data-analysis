from app.infrastructure.sql_analytics_service import SqlAnalyticsService


def test_global_activation_matches_exact_dataset_context() -> None:
    activation = {
        "test_stage": "FT",
        "supplier_id": None,
        "product_id": None,
        "parameter_pattern": None,
    }
    context = {
        "test_stage": "FT",
        "supplier_id": 7,
        "product_id": 9,
    }

    assert SqlAnalyticsService._activation_matches_context(
        activation, context, ("VTH", "VCE")
    )


def test_scoped_activation_requires_matching_supplier_product_and_all_parameters() -> None:
    activation = {
        "test_stage": "FT",
        "supplier_id": 7,
        "product_id": 9,
        "parameter_pattern": "VTH*",
    }
    context = {
        "test_stage": "FT",
        "supplier_id": 7,
        "product_id": 9,
    }

    assert SqlAnalyticsService._activation_matches_context(
        activation, context, ("VTH", "VTH_2")
    )
    assert not SqlAnalyticsService._activation_matches_context(
        activation, context, ("VTH", "VCE")
    )
    assert not SqlAnalyticsService._activation_matches_context(
        activation, {**context, "product_id": 10}, ("VTH",)
    )


def test_parameter_scoped_activation_is_not_guessed_before_parameter_selection() -> None:
    activation = {
        "test_stage": "CP",
        "supplier_id": None,
        "product_id": None,
        "parameter_pattern": "BV*",
    }
    context = {
        "test_stage": "CP",
        "supplier_id": 3,
        "product_id": 4,
    }

    assert not SqlAnalyticsService._activation_matches_context(
        activation, context, ()
    )
