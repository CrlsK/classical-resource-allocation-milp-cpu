"""DO NOT MODIFY — local test runner. Platform replaces this at runtime."""
import json
import qcentroid

with open("input.json") as f:
    dic = json.load(f)

result = qcentroid.run(
    dic["data"],
    dic.get("solver_params", {}),
    dic.get("extra_arguments", {}),
)
print(json.dumps(result, indent=2))
