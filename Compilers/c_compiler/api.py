import os
import subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Literal

app = FastAPI(title="C/C++ Compiler API")

class CodeExecutionRequest(BaseModel):
    folder_path: str
    language: Literal["c", "cpp"]
    input_file_path: str = None
    timeout: int = 10

@app.post("/run")
def run_code(request: CodeExecutionRequest):
    folder_path = request.folder_path
    language = request.language

    # Validate folder path
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=400, detail="Folder does not exist")
    if not os.path.isdir(folder_path):
        raise HTTPException(status_code=400, detail="Path must be a folder")

    # Determine file extension and compile command
    file_ext = ".cpp" if language == "cpp" else ".c"
    exec_file = os.path.join(folder_path, "a.out")  # output binary

    # Compile all source files in the folder
    compile_cmd = ["sh", "-c", f"g++ {folder_path}/*{file_ext} -o {exec_file}"] if language == "cpp" \
                  else ["sh", "-c", f"gcc {folder_path}/*{file_ext} -o {exec_file}"]

    try:
        compilation = subprocess.run(
            compile_cmd,
            capture_output=True,
            text=True,
            timeout=request.timeout
        )
        if compilation.returncode != 0:
            return {
                "stdout": compilation.stdout,
                "stderr": compilation.stderr
            }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Compilation timed out"}

    # Run the executable
    run_cmd = [exec_file]
    try:
        if request.input_file_path:
            if not os.path.exists(request.input_file_path):
                raise HTTPException(status_code=400, detail="Input file does not exist")
            with open(request.input_file_path, "r") as f:
                run_result = subprocess.run(
                    run_cmd,
                    capture_output=True,
                    text=True,
                    stdin=f,
                    timeout=request.timeout
                )
        else:
            run_result = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                timeout=request.timeout
            )
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Execution timed out"}

    return {"stdout": run_result.stdout, "stderr": run_result.stderr}
