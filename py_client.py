from Models.models import Product
import requests
from getpass import getpass
username = input('Enter your username: ')
password = getpass('Enter your password: ')

headers = {}
token_endpoint = 'http://localhost:8000/api/auth/'
token_response = requests.post(token_endpoint, json={'username': username, 'password': password})
if token_response.status_code == 200:
    print(token_response.json())
    headers = {'Authorization': 'Token ' + token_response.json()['token']}


operation = int(input("Select operation: \n1.Create \n2.List view \n3.Detail view \n4.Update view \n5.Delete View \n"))
if operation == 1 or operation == 2:
    endpoint = 'http://localhost:8000/api/products/'
    if operation == 1:

        name = input('Enter product name: ')
        price = input('Enter product price: ')
        description = input('Enter product description: ')
        quantity = input('Enter product quantity: ')
        data = { "name": name, "price": int(price) if price else None, "description": description, "quantity": int(quantity) if quantity else None }
        response = requests.post(endpoint, json=data, headers=headers)
        print(response.json())
    if operation == 2:
        response = requests.get(endpoint, headers=headers)
        print(response.json())
if operation == 3 or operation == 4 or operation == 5:
    product_id = input('Enter product id: ')
    endpoint = 'http://localhost:8000/api/products/' + product_id + '/'

    if operation == 4:
        name = input('Enter product name: ')
        price = input('Enter product price: ')
        description = input('Enter product description: ')
        quantity = input('Enter product quantity: ')

        product = Product.objects.get(id=int(product_id))


        data = { "name": name, "price": int(price) if price else product.price, "description": description if description else product.description, "quantity": int(quantity) if quantity else product.quantity }
        response = requests.put(endpoint, json=data, headers=headers)
        if response.status_code == 200:
            print(response.json())

    if operation == 3:
        response = requests.get(endpoint, headers=headers)
        if response.status_code == 200:
            print(response.json())

    if operation == 5:
        response = requests.delete(endpoint, headers=headers)
        if response.status_code == 200:
            print(response)



