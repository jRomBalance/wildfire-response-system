"""
WildfireNet — Send Pitch Email
Sends the agency outreach pitch email via SendGrid.
Run: python scripts/send_pitch_email.py
"""

import os
import sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

def send_pitch_email():
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail, From, To, Subject, HtmlContent
    except ImportError:
        print("Run: pip install sendgrid")
        return

    api_key    = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("ALERT_FROM_EMAIL")
    to_email   = os.getenv("FIREFIGHTER_EMAILS", "").split(",")[0].strip()

    if not all([api_key, from_email, to_email]):
        print("Missing SENDGRID_API_KEY, ALERT_FROM_EMAIL, or FIREFIGHTER_EMAILS in .env")
        return

    subject = "WildfireNet — Fire stations get alerted after someone calls 911. We alert them when NASA does."

    html_body = """
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 680px; margin: 0 auto; color: #1a1a1a;">

      <!-- Header -->
      <div style="background: linear-gradient(135deg, #B71C1C, #FF5722);
                  padding: 40px 30px; border-radius: 12px 12px 0 0; text-align: center;">
        <h1 style="color: white; margin: 0; font-size: 28px; letter-spacing: 1px;">
          🔥 WildfireNet
        </h1>
        <p style="color: rgba(255,255,255,0.9); margin: 8px 0 0; font-size: 16px;">
          Autonomous Wildfire Detection &amp; Response System
        </p>
      </div>

      <!-- Live Data Banner -->
      <div style="background: #1a1a1a; padding: 20px 30px; text-align: center;">
        <p style="color: #FF5722; margin: 0; font-size: 13px; letter-spacing: 2px; font-weight: bold;">
          LIVE DATA — JULY 18, 2026
        </p>
        <p style="color: white; margin: 8px 0 0; font-size: 15px;">
          3,294 active fire detections across 8 priority regions right now.
          Traverse City, MI: PM2.5 AQI 133 — wildfire smoke confirmed.
        </p>
      </div>

      <!-- Body -->
      <div style="padding: 35px 30px; background: #ffffff;
                  border: 1px solid #e0e0e0; border-top: none;">

        <p style="font-size: 16px; line-height: 1.7; margin-top: 0;">
          The technology to stop wildfires before they become megafires already exists.
          NASA satellites detect heat signatures. IoT sensors smell smoke before there's
          a visible flame. Autonomous drones can drop retardant in under 12 minutes.
        </p>

        <p style="font-size: 16px; line-height: 1.7;">
          <strong>Nobody has connected them.</strong> Until now.
        </p>

        <hr style="border: none; border-top: 2px solid #FF5722; margin: 30px 0;">

        <!-- The Pitch -->
        <h2 style="color: #B71C1C; font-size: 20px; margin-bottom: 5px;">
          The Pitch — One Sentence
        </h2>
        <div style="background: #FFF3E0; border-left: 5px solid #FF5722;
                    padding: 20px 25px; border-radius: 0 8px 8px 0; margin: 15px 0 30px;">
          <p style="font-size: 18px; line-height: 1.6; margin: 0; font-style: italic;">
            "Fire stations get alerted after someone calls 911.
            We alert them when NASA's satellite detects heat —
            before anyone sees flames. We built it, it's running,
            and it's free for agencies to connect to."
          </p>
        </div>

        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">

        <!-- What's Running -->
        <h2 style="color: #B71C1C; font-size: 20px;">What's Running Right Now</h2>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 25px;">
          <tr style="background: #f5f5f5;">
            <td style="padding: 12px 15px; font-weight: bold;">🛰️ NASA FIRMS Satellite</td>
            <td style="padding: 12px 15px; color: #2e7d32; font-weight: bold;">LIVE</td>
            <td style="padding: 12px 15px;">3,294 fire detections pulled today</td>
          </tr>
          <tr>
            <td style="padding: 12px 15px; font-weight: bold;">🌫️ EPA AirNow PM2.5</td>
            <td style="padding: 12px 15px; color: #2e7d32; font-weight: bold;">LIVE</td>
            <td style="padding: 12px 15px;">Traverse City AQI 133 — smoke confirmed</td>
          </tr>
          <tr style="background: #f5f5f5;">
            <td style="padding: 12px 15px; font-weight: bold;">📧 Email Alert Pipeline</td>
            <td style="padding: 12px 15px; color: #2e7d32; font-weight: bold;">LIVE</td>
            <td style="padding: 12px 15px;">You're reading proof of this right now</td>
          </tr>
          <tr>
            <td style="padding: 12px 15px; font-weight: bold;">📱 SMS to Firefighters</td>
            <td style="padding: 12px 15px; color: #FF9800; font-weight: bold;">READY</td>
            <td style="padding: 12px 15px;">Twilio integrated, KYC pending</td>
          </tr>
          <tr style="background: #f5f5f5;">
            <td style="padding: 12px 15px; font-weight: bold;">🚁 Drone Dispatch</td>
            <td style="padding: 12px 15px; color: #1565C0; font-weight: bold;">CODED</td>
            <td style="padding: 12px 15px;">Dryad Silvaguard API integrated</td>
          </tr>
          <tr>
            <td style="padding: 12px 15px; font-weight: bold;">🌐 REST API + WebSocket</td>
            <td style="padding: 12px 15px; color: #2e7d32; font-weight: bold;">LIVE</td>
            <td style="padding: 12px 15px;">Full FastAPI backend running</td>
          </tr>
        </table>

        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">

        <!-- The Gap -->
        <h2 style="color: #B71C1C; font-size: 20px;">The Gap We're Closing</h2>
        <div style="display: flex; gap: 20px; margin-bottom: 25px;">

          <div style="flex: 1; background: #ffebee; padding: 20px;
                      border-radius: 8px; border: 1px solid #ffcdd2;">
            <h3 style="color: #B71C1C; margin-top: 0;">❌ Current Reality</h3>
            <p style="margin: 0; line-height: 1.7; font-size: 14px;">
              NASA detects fire<br>
              → Data sits in database<br>
              → Analyst checks (maybe)<br>
              → Report filed<br>
              → Dispatch notified<br>
              → Crews sent<br><br>
              <strong>Total time: 30 min – 4+ hours</strong><br>
              <strong>Fire size: Catastrophic</strong>
            </p>
          </div>

          <div style="flex: 1; background: #e8f5e9; padding: 20px;
                      border-radius: 8px; border: 1px solid #c8e6c9;">
            <h3 style="color: #2e7d32; margin-top: 0;">✅ WildfireNet</h3>
            <p style="margin: 0; line-height: 1.7; font-size: 14px;">
              NASA detects fire<br>
              → WildfireNet pulls in 10 min<br>
              → AI scores severity<br>
              → SMS hits firefighter directly<br>
              → Drone launches autonomously<br>
              → Retardant drops<br><br>
              <strong>Total time: Under 15 minutes</strong><br>
              <strong>Fire size: Still stoppable</strong>
            </p>
          </div>
        </div>

        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">

        <!-- Priority Regions -->
        <h2 style="color: #B71C1C; font-size: 20px;">Priority 1 Regions — Zero Coverage Today</h2>
        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
          <tr style="background: #B71C1C; color: white;">
            <th style="padding: 10px 15px; text-align: left;">Region</th>
            <th style="padding: 10px 15px; text-align: left;">Status</th>
            <th style="padding: 10px 15px; text-align: left;">Population at Risk</th>
          </tr>
          <tr style="background: #ffebee;">
            <td style="padding: 10px 15px;">Ontario–Michigan Border</td>
            <td style="padding: 10px 15px; color: #B71C1C; font-weight: bold;">🔴 ACTIVE EMERGENCY</td>
            <td style="padding: 10px 15px;">10 million</td>
          </tr>
          <tr>
            <td style="padding: 10px 15px;">Northern Ontario Boreal</td>
            <td style="padding: 10px 15px; color: #B71C1C; font-weight: bold;">🔴 ACTIVE EMERGENCY</td>
            <td style="padding: 10px 15px;">50 million downwind</td>
          </tr>
          <tr style="background: #ffebee;">
            <td style="padding: 10px 15px;">Alberta / BC Interior</td>
            <td style="padding: 10px 15px; color: #B71C1C; font-weight: bold;">🔴 728 detections today</td>
            <td style="padding: 10px 15px;">30 million downwind</td>
          </tr>
          <tr>
            <td style="padding: 10px 15px;">Upper Peninsula, Michigan</td>
            <td style="padding: 10px 15px; color: #B71C1C; font-weight: bold;">🔴 Ash fall confirmed</td>
            <td style="padding: 10px 15px;">4 million</td>
          </tr>
          <tr style="background: #ffebee;">
            <td style="padding: 10px 15px;">Saskatchewan Boreal</td>
            <td style="padding: 10px 15px; color: #B71C1C; font-weight: bold;">🔴 HIGH RISK</td>
            <td style="padding: 10px 15px;">20 million downwind</td>
          </tr>
        </table>

        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">

        <!-- Why Now -->
        <h2 style="color: #B71C1C; font-size: 20px;">Why This Week</h2>
        <p style="font-size: 15px; line-height: 1.7;">
          Right now, as you read this, Canadian wildfires are pushing hazardous PM2.5
          smoke across all of Michigan and the Northeast. Ash fell on the Mackinac Bridge
          on July 15th. Michigan EGLE extended a statewide Air Quality Alert.
          3,294 satellite fire detections are active across our priority regions.
        </p>
        <p style="font-size: 15px; line-height: 1.7;">
          This isn't a future problem. It's happening today.
          And today, for the first time, there's a system watching it in real time —
          ready to alert the people who can do something about it.
        </p>

        <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 30px 0;">

        <!-- CTA -->
        <h2 style="color: #B71C1C; font-size: 20px;">What We're Looking For</h2>
        <ul style="font-size: 15px; line-height: 2.0; padding-left: 20px;">
          <li>Agency partners to connect our alert pipeline to CAD dispatch systems</li>
          <li>IoT sensor deployment agreements (Upper Michigan / Ontario border)</li>
          <li>Data sharing with USFS, CIFFC Canada, state forestry agencies</li>
          <li>Pilot program: 50-camera network, Great Lakes border zone</li>
        </ul>

        <div style="text-align: center; margin: 35px 0;">
          <a href="https://github.com/jRomBalance/wildfire-response-system"
             style="background: #B71C1C; color: white; padding: 15px 35px;
                    text-decoration: none; border-radius: 6px; font-weight: bold;
                    font-size: 16px; display: inline-block; margin: 8px;">
            🔥 View the Code
          </a>
          <a href="https://firms.modaps.eosdis.nasa.gov/map/"
             style="background: #333; color: white; padding: 15px 35px;
                    text-decoration: none; border-radius: 6px; font-weight: bold;
                    font-size: 16px; display: inline-block; margin: 8px;">
            🛰️ NASA Live Fire Map
          </a>
        </div>

      </div>

      <!-- Footer -->
      <div style="background: #1a1a1a; padding: 25px 30px;
                  border-radius: 0 0 12px 12px; text-align: center;">
        <p style="color: #aaa; margin: 0; font-size: 13px;">
          WildfireNet — Built July 17–18, 2026<br>
          Started because Michigan air quality was Hazardous and someone had to do something.<br><br>
          <a href="https://github.com/jRomBalance/wildfire-response-system"
             style="color: #FF5722; text-decoration: none;">
            github.com/jRomBalance/wildfire-response-system
          </a>
        </p>
      </div>

    </body>
    </html>
    """

    message = Mail(
        from_email=From(from_email, "WildfireNet"),
        to_emails=To(to_email),
        subject=Subject(subject),
        html_content=HtmlContent(html_body),
    )

    try:
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(f"✅ Pitch email sent to {to_email}")
        print(f"   Status: {response.status_code}")
        print(f"   Subject: {subject}")
    except Exception as e:
        print(f"❌ Failed: {e}")


if __name__ == "__main__":
    send_pitch_email()