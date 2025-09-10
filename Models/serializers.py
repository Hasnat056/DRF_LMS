import shutil, tempfile, os, subprocess, zipfile
from rest_framework import serializers
from rest_framework.response import Response
from django.core.files.uploadedfile import InMemoryUploadedFile
import uuid




class CompilerSerializer(serializers.Serializer):
    file = serializers.ListSerializer(
        child=serializers.FileField(),
    )
    input_list = serializers.CharField(
        required=False,
        allow_null=True,
        style={'base_template': 'textarea.html'},

    )

    class Meta:
        fields = [
            'file',
            'input_list',
        ]

    def to_internal_value(self, data):
        if hasattr(data, "getlist"):  # QueryDict
            files = [f for f in data.getlist("file") if f]  # filter out None/empty
            normalized = {
                "file": files,
                "input_list": data.get("input_list"),
            }
        else:
            file_field = data.get("file")
            if isinstance(file_field, InMemoryUploadedFile):
                files = [file_field]
            elif isinstance(file_field, list):
                # make sure it's flat list of file objects only
                files = [f for f in file_field if isinstance(f, InMemoryUploadedFile)]
            else:
                files = []
            normalized = {
                "file": files,
                "input_list": data.get("input_list"),
            }

        return super().to_internal_value(normalized)

    def create(self, validated_data):
        python_container = "Python"
        C_container = "Cpp"
        uploaded_file = validated_data.pop('file')
        input_file = None
        # parsing input file
        print(validated_data['input_list'])
        if 'input_list' in validated_data and validated_data['input_list'] is not None:
            file_inputs = validated_data.pop('input_list')

            parsed_inputs = file_inputs.split('\n')
            parsed_inputs = [lines.strip() for lines in parsed_inputs]

            with tempfile.NamedTemporaryFile(delete=False, suffix='.txt', mode='w') as inputFile:
                for each in parsed_inputs:
                    inputFile.write(each + '\n')

            # input file path
            input_file = inputFile.name

        try:
            # 1. Handle single file (.py or .zip)
            if len(uploaded_file) == 1:
                file_obj = uploaded_file[0]
                extension = file_obj.name.split('.')[-1]

                if extension == 'py' or extension == 'c' or extension == 'cpp':
                    if extension == 'cpp' or extension == 'c':
                        return self._handle_single_file(file_obj, input_file, C_container, extension)
                    if extension == 'py':
                        return self._handle_single_file(file_obj, input_file, python_container, extension)

                elif extension == 'zip':
                    return self._handle_zip(file_obj, input_file)

                else:
                    return Response({'error': 'Invalid file extension. Supported extensions: .c, .cpp, .py, .zip'})

            # 2. Handle multiple files
            elif len(uploaded_file) > 1:

                return self._handle_multiple_files(uploaded_file, input_file)

            else:
                return Response({'error': 'No file provided'})

        except subprocess.CalledProcessError as e:
            return Response({"stdout": "", "stderr": str(e)})

    def _setup_container(self, container_name):

        # check if the container exists
        # docker ps -a  -> returns list of all existing containers
        # -q -> this tag causes the list of containers to only return container_ids
        # -f -> filter tag
        result = subprocess.run(
            ["docker", "ps", "-a", "-q", "-f", f"name={container_name}"],
            capture_output=True, text=True
        )
        container_id = result.stdout.strip()

        # if container not found, run a new container
        # docker run -> runs a container from an image
        # -dit : -d{detach mode), -it{interactive mode}, -> keeps the container running in background and allows to run script on its terminal
        # --name -> use to set container name
        # tail -f /dev/null -> keeps container running in idle mode
        if not container_id:
            if container_name == 'Python':
                subprocess.run([
                    "docker", "run", "-dit", "--name", container_name,
                    "python:3.13.1", "tail", "-f", "/dev/null"
                ], check=True)
            if container_name == 'Cpp':
                subprocess.run([
                    "docker", "run", "-dit", "--name", container_name,
                    "gcc:latest", "tail", "-f", "/dev/null"
                ], check=True)

        # if container found then start it
        else:
            subprocess.run(["docker", "start", container_name], check=True)

        # check for /code directory inside container
        # docker exec -> run a command on a running container
        # -p -> supress errors from mkdir(raises error if directory already exists)
        subprocess.run(
            ["docker", "exec", container_name, "mkdir", "-p", "/code"],
            check=True
        )

    def _handle_single_file(self, file_obj, input_file, container_name, extension):
        request_folder = f"/code/{uuid.uuid4().hex}"
        with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{extension}') as tmpfile:
            tmpfile.write(file_obj.read())
            path = tmpfile.name  # this gives complete path of the file
            filename = os.path.basename(path)  # this gives name of file

        try:

            self._setup_container(container_name)

            # copying file from local machine's temporary folder to /code directory inside container

            subprocess.run(
                ["docker", "cp", path, f"{container_name}:{request_folder}/{filename}"],
                check=True
            )

            cmd = []
            # running python script on Python container on docker
            if extension == 'py':
                cmd = ["docker", "exec", '-i', container_name, "python", f"{request_folder}/{filename}"]

            if extension == 'cpp':
                compile_cmd = ["docker", "exec", container_name, "g++", f"{request_folder}/{filename}", "-o", "/code/a.out"]
                compilation = subprocess.run(compile_cmd, capture_output=True, text=True)
                if compilation.stderr:
                    return Response({"compilation error": compilation.stderr})
                cmd = ["docker", "exec", "-i", container_name, "/code/a.out"]

            if extension == 'c':
                compile_cmd = ["docker", "exec", container_name, "gcc", f"{request_folder}/{filename}", "-o", "/code/a.out"]
                compilation = subprocess.run(compile_cmd, capture_output=True, text=True)
                if compilation.stderr:
                    return Response({"compilation error": compilation.stderr})
                cmd = ["docker", "exec", "-i", container_name, "/code/a.out"]

            if input_file is not None:
                with open(input_file, "r") as f:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                            stdin=f)
                    stdout, stderr = result.stdout, result.stderr


            else:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                stdout, stderr = result.stdout, result.stderr

        finally:
            # deleting temporary file
            os.remove(path)
            subprocess.run(["docker", "exec", container_name, 'rm', '-rf', request_folder], check=True)
            if input_file is not None:
                os.remove(input_file)


        return Response({"stdout": stdout, "stderr": stderr})

    def _handle_zip(self, file_obj, input_file):
        request_folder = f"/code/{uuid.uuid4().hex}"
        file_extension = None
        container_name = None
        # creating a temporary folder
        with tempfile.TemporaryDirectory(delete=False) as tmpdir:
            # constructing a path for .zip file inside folder to write uploaded file inside temporary file
            zip_file_path = os.path.join(tmpdir, file_obj.name)

            # writing zip file data on temporary file
            with open(zip_file_path, 'wb') as f:
                f.write(file_obj.read())

            # unzipping the zip file
            with zipfile.ZipFile(zip_file_path, 'r') as zipObj:
                # checking for main.py / main.cpp / main.c
                for each in zipObj.namelist():
                    filename = os.path.basename(each)
                    name = filename.split('.')[0]
                    if name == 'main':
                        file_type = filename.split('.')[-1]
                        if file_type == 'py':
                            file_extension = 'py'
                            container_name = 'Python'
                        elif file_type == 'cpp':
                            file_extension = 'cpp'
                            container_name = 'Cpp'
                        elif file_type == 'c':
                            file_extension = 'c'
                            container_name = 'Cpp'
                        else:
                            return Response({'error': 'Languages not supported'})
                        break

                if file_extension is None:
                    return Response({'error': 'Please provide a main file with proper extension'})

                zipObj.extractall(path=tmpdir)

            extracted_folder = tmpdir
            items = os.listdir(tmpdir)
            # if there’s only one directory inside tmpdir (besides the .zip file), go into it
            dirs_only = [d for d in items if os.path.isdir(os.path.join(tmpdir, d))]
            if len(dirs_only) == 1:
                extracted_folder = os.path.join(tmpdir, dirs_only[0])

        try:
            self._setup_container(container_name)

            subprocess.run(
                ["docker", "cp", f"{extracted_folder}/.", f"{container_name}:{request_folder}"],
                check=True
            )

            cmd = []
            # running python script
            if file_extension == 'py':
                cmd = ["docker", "exec", '-i', container_name, "python", f"{request_folder}/main.py"]

            if file_extension == 'cpp':

                compile_cmd = ["docker", "exec", container_name, 'sh', '-c', f"g++ {request_folder}/*.cpp -o {request_folder}/a.out"]
                compilation = subprocess.run(compile_cmd, capture_output=True, text=True)
                if compilation.stderr:
                    return Response({"compilation error": compilation.stderr})
                cmd = ["docker", "exec", "-i", container_name, f"{request_folder}/a.out"]

            if file_extension == 'c':
                compile_cmd = ["docker", "exec", container_name, 'sh', '-c' f"gcc {request_folder}/*.c -o {request_folder}/a.out"]
                compilation = subprocess.run(compile_cmd, capture_output=True, text=True)
                if compilation.stderr:
                    return Response({"compilation error": compilation.stderr})
                cmd = ["docker", "exec", "-i", container_name, f"{request_folder}/a.out"]

            if input_file is not None:
                with open(input_file, "r") as f:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                                            stdin=f)
                    stdout, stderr = result.stdout, result.stderr
            else:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                stdout, stderr = result.stdout, result.stderr

        finally:
            os.remove(zip_file_path)
            subprocess.run(["docker", "exec", container_name, 'rm', '-rf', request_folder], check=True)
            if input_file is not None:
                os.remove(input_file)

        return Response({"stdout": stdout, "stderr": stderr})

    def _handle_multiple_files(self, uploaded_files, input_file):
        request_folder = f"/code/{uuid.uuid4().hex}"
        container_name = None
        file_extension = None

        for each in uploaded_files:
            file_path = os.path.join(each.name)
            file_name = os.path.basename(file_path)
            name = file_name.split('.')[0]
            if name == 'main':
                file_extension = file_name.split('.')[-1]
                if file_extension == 'py':
                    container_name = 'Python'
                elif file_extension == 'cpp':
                    container_name = 'Cpp'
                elif file_extension == 'c':
                    container_name = 'Cpp'
                else:
                    return Response({'error': 'Languages not supported'})
                break

        if file_extension is None:
            return Response({'error': 'Please provide a main file with proper extension'})

        with tempfile.TemporaryDirectory(delete=False) as tmpdir:
            for each in uploaded_files:
                file_path = os.path.join(tmpdir, each.name)

                with open(file_path, 'wb') as f:
                    f.write(each.read())

            folder_path = tmpdir
            print(tmpdir)

        try:
            self._setup_container(container_name)



            subprocess.run(
                ["docker", "cp", f"{folder_path}/.", f"{container_name}:{request_folder}"],
                check=True
            )

            cmd = []
            # running python script on Python container on docker
            if file_extension == 'py':
                cmd = ["docker", "exec", '-i', container_name, "python", f"{request_folder}/main.py"]

            if file_extension == 'cpp':
                subprocess.run(['docker', 'exec', '-it', container_name, 'ls', '/code'], check=True)

                compile_cmd = ["docker", "exec", container_name, 'sh', '-c', f"g++ {request_folder}/*.cpp -o {request_folder}/a.out"]
                compilation = subprocess.run(compile_cmd, capture_output=True, text=True)
                if compilation.stderr:
                    return Response({"compilation error": compilation.stderr})
                cmd = ["docker", "exec", "-i", container_name, f"{request_folder}/a.out"]

            if file_extension == 'c':
                compile_cmd = ["docker", "exec", container_name, 'sh', '-c' f"gcc {request_folder}/*.c -o {request_folder}/a.out"]
                compilation = subprocess.run(compile_cmd, capture_output=True, text=True)
                if compilation.stderr:
                    return Response({"compilation error": compilation.stderr})
                cmd = ["docker", "exec", "-i", container_name, f"{request_folder}/a.out"]

            if input_file is not None:
                with open(input_file, "r") as f:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=25,
                                            stdin=f)
                    stdout, stderr = result.stdout, result.stderr
            else:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
                stdout, stderr = result.stdout, result.stderr


        finally:
            shutil.rmtree(folder_path, ignore_errors=True)
            subprocess.run(["docker", "exec", container_name, 'rm', '-rf', request_folder], check=True)
            if input_file is not None:
                os.remove(input_file)

        return Response({"stdout": stdout, "stderr": stderr})