from main import build_parser


def test_cli_accepts_kst_local_modes_and_root():
    parser = build_parser()

    fetch = parser.parse_args(
        [
            "--mode",
            "fetch-kst-local",
            "--project",
            "kunming_niu",
            "--date",
            "2026-07-27",
            "--kst-root",
            r"D:\Program Files (x86)\KuaishangSoftx64\OnlineWebCSNew",
        ]
    )
    serve = parser.parse_args(
        [
            "--mode",
            "serve-kst-local",
            "--host",
            "127.0.0.1",
            "--port",
            "18766",
        ]
    )

    assert fetch.mode == "fetch-kst-local"
    assert fetch.kst_root.endswith("OnlineWebCSNew")
    assert serve.mode == "serve-kst-local"
    assert serve.host == "127.0.0.1"
    assert serve.port == 18766
