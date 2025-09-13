from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import os

app = FastAPI(title="Python Compiler API")

class CodeExecutionRequest(BaseModel):
    file_path: str           # full path inside container: /code/<uuid>/main.py
    input_file_path: str = None
    timeout: int = 10

@app.post("/run")
def run_code(request: CodeExecutionRequest):
    file_path = request.file_path

    if not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="File does not exist")

    cmd = ["python3", file_path]

    try:
        if request.input_file_path:
            if not os.path.exists(request.input_file_path):
                raise HTTPException(status_code=400, detail="Input file does not exist")
            with open(request.input_file_path, "r") as f:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        stdin=f, timeout=request.timeout)
        else:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=request.timeout)
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Execution timed out"}

    return {
        "stdout": result.stdout,
        "stderr": result.stderr
    }
