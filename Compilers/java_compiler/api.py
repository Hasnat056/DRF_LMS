from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import os

app = FastAPI(title="Java Compiler API")

class CodeExecutionRequest(BaseModel):
    file_path: str           # main Java file inside /code
    input_file_path: str = None
    timeout: int = 10

@app.post("/run")
def run_code(request: CodeExecutionRequest):
    file_path = request.file_path

    if not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="File does not exist")

    if not file_path.endswith(".java"):
        raise HTTPException(status_code=400, detail="Unsupported file type")

    # Compile Java code
    compile_cmd = ["javac", file_path]
    try:
        compile_result = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=request.timeout)
        if compile_result.returncode != 0:
            return {"stdout": compile_result.stdout, "stderr": compile_result.stderr}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Compilation timed out"}

    # Determine class name (strip path and .java)
    class_name = os.path.splitext(os.path.basename(file_path))[0]

    # Run Java program
    run_cmd = ["java", "-cp", os.path.dirname(file_path), class_name]
    try:
        if request.input_file_path:
            if not os.path.exists(request.input_file_path):
                raise HTTPException(status_code=400, detail="Input file does not exist")
            with open(request.input_file_path, "r") as f:
                run_result = subprocess.run(run_cmd, capture_output=True, text=True, stdin=f, timeout=request.timeout)
        else:
            run_result = subprocess.run(run_cmd, capture_output=True, text=True, timeout=request.timeout)
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Execution timed out"}

    return {"stdout": run_result.stdout, "stderr": run_result.stderr}
