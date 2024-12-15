# slack.py


from http.client import HTTPException

from fastapi.responses import HTMLResponse
from fastapi import Request
import secrets
import json
import base64
import httpx
import asyncio
import requests

from integrations.integration_item import IntegrationItem
from redis_client import add_key_value_redis, get_value_redis, delete_key_redis

CLIENT_ID = '87156200-1583-44f7-88ef-2edbb5e4712e'
CLIENT_SECRET = '7ad60cca-ec02-4434-8490-92ac0b5178ab'
REDIRECT_URI = 'http://localhost:8000/integrations/hubspot/oauth2callback'
authorization_url = f'https://app.hubspot.com/oauth/authorize?client_id={CLIENT_ID}&response_type=code&owner=user&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fintegrations%2Fhubspot%2Foauth2callback'

encoded_client_id_secret = base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()
scope = 'crm.objects.contacts.read'


async def authorize_hubspot(user_id, org_id):
    state_data = {
        'state': secrets.token_urlsafe(32),
        'user_id': user_id,
        'org_id': org_id
    }
    encoded_state = json.dumps(state_data)
    await add_key_value_redis(f'hubspot_state:{org_id}:{user_id}', encoded_state, expire=600)

    return f'{authorization_url}&state={encoded_state}&scope={scope}'

async def oauth2callback_hubspot(request: Request):
    if request.query_params.get('error'):
        raise HTTPException(status_code=400, detail=request.query_params.get('error'))

    code = request.query_params.get('code')
    state = request.query_params.get('state')
    state_data = json.loads(state)

    original_state = state_data.get('state')
    user_id = state_data.get('user_id')
    org_id = state_data.get('org_id')

    saved_state_encoded = await get_value_redis(f'hubspot_state:{org_id}:{user_id}')
    saved_state = json.loads(saved_state_encoded.decode("utf-8")).get('state')

    if not saved_state or original_state != saved_state:
        raise HTTPException(status_code=400, detail='State does not match.')

    async with httpx.AsyncClient() as client:
        response, _ = await asyncio.gather(
            client.post(
                'https://api.hubapi.com/oauth/v1/token',
                json={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': REDIRECT_URI,
                    'client_id': CLIENT_ID,
                    'client_secret': CLIENT_SECRET,
                },
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': REDIRECT_URI,
                    'client_id': CLIENT_ID,
                    'client_secret': CLIENT_SECRET,
                },
                headers={
                    # 'Authorization': f'Basic {encoded_client_id_secret}',
                    'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8',
                    'Content-Type': 'application/x-www-form-urlencoded',
                }
            ),
            delete_key_redis(f'hubspot_state:{org_id}:{user_id}'),
        )

    await add_key_value_redis(f'hubspot_credentials:{org_id}:{user_id}', json.dumps(response.json()), expire=600)

    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
    </html>
    """
    return HTMLResponse(content=close_window_script)

async def get_hubspot_credentials(user_id, org_id):
    credentials = await get_value_redis(f'hubspot_credentials:{org_id}:{user_id}')
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    credentials = json.loads(credentials)
    if not credentials:
        raise HTTPException(status_code=400, detail='No credentials found.')
    await delete_key_redis(f'hubspot_credentials:{org_id}:{user_id}')

    return credentials

def create_integration_item_metadata_object(response_json) -> IntegrationItem:
    properties = response_json['properties']
    firstname = properties['firstname']
    lastname = properties['lastname']
    integration_item_metadata = IntegrationItem(
        id=response_json['id'],
        name=f"{firstname} {lastname}",
        creation_time=properties['createdate'],
        last_modified_time=properties['lastmodifieddate'],
        visibility=response_json['archived'],
    )

    return integration_item_metadata

async def get_items_hubspot(credentials):
    """Aggregates all metadata relevant for a hubspot integration"""
    credentials = json.loads(credentials)

    payload = {
      "limit": 100,
    }

    response = requests.post(
        'https://api.hubapi.com/crm/v3/objects/contacts/search',
        headers={
            'Authorization': f'Bearer {credentials.get("access_token")}',
            'Content-Type': 'application/json',
        },
        data=json.dumps(payload),
    )

    if response.status_code == 200:
        results = response.json()['results']
        list_of_integration_item_metadata = []
        for result in results:
            list_of_integration_item_metadata.append(
                create_integration_item_metadata_object(result)
            )

        for item in list_of_integration_item_metadata:
            print(item)

    return list_of_integration_item_metadata