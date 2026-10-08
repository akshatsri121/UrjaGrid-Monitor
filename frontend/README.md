# Frontend

`index.html` is the working microgrid history frontend. Run the server in
`../backend/` and open http://127.0.0.1:8000. Do not open this HTML via file://;
it needs the backend API on the same origin.

Your Blynk dashboard remains in your Blynk account. Its dashboard configuration
has not been exported into this repository. Virtual-pin assignments and setup
instructions are in the firmware and `../docs/WIRING_AND_SETUP.md`.

`reference/dashboard.html` and `reference/requirements.txt` are the exact
Desktop files you supplied. The HTML is incomplete and refers to acoustic
testing, not this microgrid. They are retained as source references and are
not served by the backend. The reference requirements describe a separate
serial/WebSocket approach; use `../backend/requirements.txt` for this backend.
