import threading

from app.stall_watchdog import format_thread_stacks


def test_the_stack_dump_shows_where_a_stuck_thread_is_waiting():
    release = threading.Event()

    def stuck_in_an_obs_call():
        release.wait(10)

    t = threading.Thread(target=stuck_in_an_obs_call, name="StuckWorker", daemon=True)
    t.start()
    try:
        text = ""
        for _ in range(100):
            text = format_thread_stacks()
            if "stuck_in_an_obs_call" in text:
                break
            threading.Event().wait(0.02)
        assert "--- thread StuckWorker ---" in text
        assert "stuck_in_an_obs_call" in text
        assert "--- thread MainThread ---" in text
    finally:
        release.set()
        t.join(2)
