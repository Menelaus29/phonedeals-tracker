import subprocess, sys
r = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/", "--tb=short", "-v"],
    capture_output=True, text=True
)
output = r.stdout + r.stderr
# Write to file so we can read it cleanly
with open("test_results.txt", "w", encoding="utf-8") as f:
    f.write(output)
print(output)
sys.exit(r.returncode)
