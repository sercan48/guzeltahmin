# Read UTF-16LE encoded pipeline_test.log file and print the last 50 lines.
import os

log_path = "pipeline_test.log"
if os.path.exists(log_path):
    try:
        with open(log_path, "r", encoding="utf-16-le") as f:
            lines = f.readlines()
        print(f"Total lines in {log_path}: {len(lines)}")
        print("Last 50 lines:")
        for line in lines[-50:]:
            print(line, end="")
    except Exception as e:
        print(f"Error reading with utf-16-le: {e}")
else:
    print(f"File {log_path} does not exist")
