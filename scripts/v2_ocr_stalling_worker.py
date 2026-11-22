"""TEST ONLY. The real OCR worker, except that while the file named by STALL_COUNT_FILE holds fewer than STALL_JOBS
completed stalls, a job hangs forever instead of being read (as a Vision call wedged in native code would). It is
only ever started by scripts/v2_ocr_stall_demo.py, which passes it to OcrWorkerClient(command=...). Nothing in
trace_v2 reads these variables or offers a way to select this file."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trace_v2.macos import ocr_worker  # noqa: E402


def stalling_handler(ocr, header, body, out_fd):
    counter, limit = Path(os.environ["STALL_COUNT_FILE"]), int(os.environ["STALL_JOBS"])
    done = int(counter.read_text() or 0) if counter.exists() else 0
    if done < limit:
        counter.write_text(str(done + 1))
        time.sleep(10 ** 6)
    return ocr_worker.handle_job(ocr, header, body, out_fd)


if __name__ == "__main__":
    sys.exit(ocr_worker.main(sys.argv[1:], handler=stalling_handler))
