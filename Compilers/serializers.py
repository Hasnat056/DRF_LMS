import shutil, os, subprocess, zipfile
import requests
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
        request_folder = f"/code/{uuid.uuid4().hex}"
        os.makedirs(request_folder, exist_ok=True)

        uploaded_file = validated_data.pop('file')
        input_file = None
        # parsing input file
        print(validated_data['input_list'])
        if 'input_list' in validated_data and validated_data['input_list'] is not None:
            file_inputs = validated_data.pop('input_list')

            parsed_inputs = file_inputs.split('\n')
            parsed_inputs = [lines.strip() for lines in parsed_inputs]

            with open(os.path.join(request_folder, 'input.txt'), 'w') as inputFile:
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
                        return self._handle_single_file(request_folder,file_obj, input_file, extension)
                    if extension == 'py':
                        return self._handle_single_file(request_folder,file_obj, input_file, extension)

                elif extension == 'zip':
                    return self._handle_zip(file_obj, input_file, request_folder)

                else:
                    return Response({'error': 'Invalid file extension. Supported extensions: .c_compiler, .cpp, .py, .zip'})

            # 2. Handle multiple files
            elif len(uploaded_file) > 1:

                return self._handle_multiple_files(uploaded_file, input_file, request_folder)

            else:
                return Response({'error': 'No file provided'})

        except subprocess.CalledProcessError as e:
            return Response({"stdout": "", "stderr": str(e)})


    def _handle_single_file(self,request_folder, file_obj, input_file,extension):
        file_path = os.path.join(request_folder, file_obj.name)
        with open(file_path, 'wb') as f:
            for chunk in file_obj.chunks():
                f.write(chunk)

        response = None
        try:

            if extension == 'py':
                url = 'http://python-compiler:8000/run'
                data = {
                    'file_path': file_path,
                    'input_file_path': input_file,
                    'timeout': 15
                }
                response = requests.post(url, data=data)

            elif extension == 'cpp' or extension == 'c':
                url = 'http://c-compiler:8000/run'
                data = {
                    'folder_path': request_folder,
                    'language': 'c' if extension == 'c' else 'cpp',
                    'input_file_path': input_file,
                    'timeout': 15
                }
                response = requests.post(url, json=data)

        except requests.exceptions.RequestException as e:
            return Response({"stdout": "", "stderr": str(e)})

        finally:
            shutil.rmtree(request_folder, ignore_errors=True)

        return Response(response.json())


    def _handle_zip(self, file_obj, input_file, request_folder):
        file_extension = None

        file_path = os.path.join(request_folder, file_obj.name)
        with open(file_path, 'wb') as f:
            for chunk in file_obj.chunks():
                f.write(chunk)

            # unzipping the zip file
        with zipfile.ZipFile(file_path, 'r') as zipObj:
            # checking for main.py / main.cpp / main.c_compiler
            for each in zipObj.namelist():
                filename = os.path.basename(each)
                name = filename.split('.')[0]
                if name == 'main':
                    file_extension = filename.split('.')[-1]
                    if file_extension not in ['py', 'c', 'cpp']:
                        return Response({'error': 'Languages not supported'})
                    break

            if file_extension is None:
                return Response({'error': 'Please provide a main file with proper extension'})

            zipObj.extractall(path=request_folder)
            extracted_folder = request_folder
            items = os.listdir(request_folder)
            # if there’s only one directory inside tmpdir (besides the .zip file), go into it
            dirs_only = [d for d in items if os.path.isdir(os.path.join(request_folder, d))]
            if len(dirs_only) == 1:
                extracted_folder = os.path.join(request_folder, dirs_only[0])

        try:

            if file_extension == 'py':
                url = 'http://python-compiler:8000/run'
                data = {
                    'file_path': f'{extracted_folder}/main.py',
                    'input_file_path': input_file,
                    'timeout': 15
                }
                response = requests.post(url, data=data)

            elif file_extension == 'cpp' or file_extension == 'c':
                url = 'http://c-compiler:8000/run'
                print(extracted_folder)
                data = {
                    'folder_path': extracted_folder,
                    'language' : 'c' if file_extension == 'c' else 'cpp',
                    'input_file_path': input_file,
                    'timeout': 15
                }
                response = requests.post(url, json=data)

        except requests.exceptions.RequestException as e:
            return Response({"stdout": "", "stderr": str(e)})

        finally:
            shutil.rmtree(extracted_folder, ignore_errors=True)


        return Response(response.json())

    def _handle_multiple_files(self, uploaded_files, input_file, request_folder):
        file_extension = None

        for each in uploaded_files:
            file_path = os.path.join(each.name)
            file_name = os.path.basename(file_path)
            name = file_name.split('.')[0]
            if name == 'main':
                file_extension = file_name.split('.')[-1]
                if file_extension not in ['py', 'c', 'cpp']:
                    return Response({'error': 'Languages not supported'})
                break

        if file_extension is None:
            return Response({'error': 'Please provide a main file with proper extension'})


        for each in uploaded_files:
            file_path = os.path.join(request_folder, each.name)
            with open(file_path, 'wb') as f:
                for chunk in each.chunks():
                    f.write(chunk)

        try:
            if file_extension == 'py':
                url = 'http://python-compiler:8000/run'
                data = {
                    'file_path': f'{request_folder}/main.py',
                    'input_file_path': input_file,
                    'timeout': 15
                }
                response = requests.post(url, data=data)

            elif file_extension == 'cpp' or file_extension == 'c':
                url = 'http://c-compiler:8000/run'
                data = {
                    'folder_path': request_folder,
                    'language': 'c' if file_extension == 'c' else 'cpp',
                    'input_file_path': input_file,
                    'timeout': 15
                }
                response = requests.post(url, json=data)

        finally:
            shutil.rmtree(request_folder, ignore_errors=True)

        return Response(response.json())