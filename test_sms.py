from twilio.rest import Client

account_sid = "AC1edd439d88fcf6043254872127eb6b48"
auth_token  = "21172413e7b97c02b5e5ed8c357e3771"
from_number = "+17372583478"
to_number   = "+14134296942"

client = Client(account_sid, auth_token)

message = client.messages.create(
    body="Sent from your Twilio trial account - WildfireNet test fire alert",
    from_=from_number,
    to=to_number,
)

print(f"Status: {message.status}")
print(f"SID: {message.sid}")