from src.alerts.history import (
    get_last_state,
    update_state,
)

from src.alerts.rules import (
    check_signal_alerts,
    check_confidence_alerts,
    check_score_alerts,
    check_trend_alerts,
    check_volume_alerts,
    check_target_alerts,
)


def process_alerts(symbol, result):
    """
    Generate only NEW alerts.
    """

    previous = get_last_state(symbol)

    alerts = []

    # First scan
    if previous is None:

        update_state(symbol, result)

        return [
            {
                "type": "INITIAL",
                "priority": 1,
                "message": (
                    f"Initial signal: {result['signal']}"
                )
            }
        ]

    alerts.extend(
        check_signal_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_confidence_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_score_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_trend_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_volume_alerts(
            previous,
            result
        )
    )

    alerts.extend(
        check_target_alerts(
            previous,
            result
        )
    )

    update_state(symbol, result)

    return alerts