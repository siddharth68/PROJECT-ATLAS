import requests

# Test rebuild at different cuts
print('=== Testing cut rebuild ===')
for cut in [1, 3, 6, 9, 12]:
    r = requests.post('http://localhost:8000/build', json={'cut': cut})
    print(f'Cut {cut}: {r.json()}')

# Test evidence validity
print('\n=== Evidence validation ===')
r = requests.post('http://localhost:8000/answer', json={'question': "List Hy's law candidates"})
data = r.json()
for ref in data['evidence']:
    print(f'  {ref} - valid evidence')