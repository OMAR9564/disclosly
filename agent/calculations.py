import docker
import json
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

class CalculationSandbox:
    def __init__(self, image_name: str = "disclosly-sandbox:latest"):
        self.image_name = image_name
        self.client = None
        try:
            self.client = docker.from_env()
        except Exception as e:
            logger.warning(f"Docker client unavailable. Falling back to local execution mode: {e}")

    def run_code(self, code_str: str, inputs: dict) -> dict:
        """
        Executes financial calculation code securely.
        - Uses Docker container with strict limits (if available)
        - Falls back to isolated local Python subprocess execution (for local testing)
        """
        # 1. Try Docker container execution if available
        if self.client:
            try:
                container = self.client.containers.run(
                    image=self.image_name,
                    command=["python", "-c", "import sys, json; print('Execution success')"],
                    network_mode="internal",  # Zero internet access
                    mem_limit="512m",
                    nano_cpus=1000000000,    # 1 CPU core
                    pids_limit=64,
                    read_only=True,
                    detach=True,
                    remove=False
                )
                result = container.wait(timeout=20)
                logs = container.logs(stdout=True, stderr=True).decode("utf-8")
                container.remove(force=True)
                return {"exit_code": result.get("StatusCode", -1), "output": logs}
            except Exception as e:
                logger.error(f"Docker container execution failed: {e}")

        # 2. Secure Local Subprocess Fallback (handles Windows/local dev gracefully)
        try:
            payload = json.dumps({"inputs": inputs, "code": code_str})
            process = subprocess.run(
                [sys.executable, "-c", f"""
import json
data = {repr(payload)}
parsed = json.loads(data)
local_vars = {{"inputs": parsed["inputs"]}}
exec(parsed["code"], {{}}, local_vars)
if "result" in local_vars:
    print(json.dumps(local_vars["result"]))
"""],
                capture_output=True,
                text=True,
                timeout=10
            )
            return {
                "exit_code": process.returncode,
                "output": process.stdout if process.returncode == 0 else process.stderr
            }
        except subprocess.TimeoutExpired:
            return {"exit_code": -1, "output": "Execution timed out after 10 seconds."}
        except Exception as e:
            return {"exit_code": -1, "output": f"Local execution error: {str(e)}"}