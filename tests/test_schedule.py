"""Horario de atención calculado con la hora de Colombia."""

from datetime import datetime, timezone

from app.utils import BOGOTA, schedule_status


def test_abierto_dentro_del_horario():
    status = schedule_status(
        "11:00", "22:30", datetime(2026, 1, 5, 13, 0, tzinfo=BOGOTA)
    )
    assert status["is_open"] is True
    assert status["status_label"] == "Abierto ahora"


def test_cerrado_antes_y_despues_del_horario():
    for hour, minute in ((10, 59), (22, 30), (23, 15)):
        status = schedule_status(
            "11:00", "22:30", datetime(2026, 1, 5, hour, minute, tzinfo=BOGOTA)
        )
        assert status["is_open"] is False
        assert status["status_label"] == "Cerrado"


def test_horario_que_cruza_la_medianoche():
    assert schedule_status(
        "18:00", "02:00", datetime(2026, 1, 5, 23, 0, tzinfo=BOGOTA)
    )["is_open"]
    assert schedule_status(
        "18:00", "02:00", datetime(2026, 1, 6, 1, 0, tzinfo=BOGOTA)
    )["is_open"]
    assert not schedule_status(
        "18:00", "02:00", datetime(2026, 1, 6, 12, 0, tzinfo=BOGOTA)
    )["is_open"]


def test_convierte_utc_a_hora_colombiana():
    # 18:00 UTC = 13:00 en Colombia: abierto.
    status = schedule_status(
        "11:00", "22:30", datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)
    )
    assert status["is_open"] is True
    # 03:00 UTC del día siguiente = 22:00 en Colombia: todavía abierto.
    status = schedule_status(
        "11:00", "22:30", datetime(2026, 1, 6, 3, 0, tzinfo=timezone.utc)
    )
    assert status["is_open"] is True
