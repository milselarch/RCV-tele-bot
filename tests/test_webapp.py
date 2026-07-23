import requests

endpoint = "https://rcvdev.milselarch.com/backend_prod/fetch_poll"
data = {'poll_id': 48}
headers = {'Content-Type': 'application/json'}
timeout = 30  # seconds
response = requests.post(
    endpoint, json=data, headers=headers, timeout=timeout
)
print('response', response)
print(response.content)
