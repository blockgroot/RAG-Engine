# Connecting Google Drive and Slack

Handbook reads your Google Drive and Slack through **apps that belong to your company**. You create each app once, copy its keys into Handbook, and then connect as usual. Handbook shows every value you need, with a **Copy** button next to it.

**Why your own app?**
- **Google:** an app that belongs to your company is "Internal". Everyone in your company can connect right away, with no Google review and no need to add people one by one.
- **Slack:** Slack limits apps shared between many companies to reading about one page of messages a minute. Your own app reads at full speed.
- **Your data stays between your company and Handbook.** You can remove the app at any time.

**Who should do this:** a Handbook admin who is also an admin in Google Workspace (for Google) or allowed to install apps in Slack (for Slack). No technical knowledge is needed.

**Time:** about 10 minutes for Google, 5 for Slack.

---

## Google Drive

**You need:** a Google Workspace account (an email at your company's domain, not @gmail.com).

1. **Open the setup in Handbook.** Go to **Sources**. Under **Your company's apps**, press **Set up** on the **Google app** card. Handbook shows one step at a time, with a button that opens the right Google page. Keep Handbook open; you'll come back to it.

2. **Create a project in Google Cloud.**
   - Open [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate), signed in with your company Google account.
   - Project name: **Handbook**. Press **Create**.
   - Wait a few seconds until Google says the project is ready. Check the project picker at the top of the page shows **Handbook**.

3. **Turn on four Google services.** Handbook links to each one. Open each link, check the project at the top is **Handbook**, and press **Enable**:
   - Google Drive API
   - Google Docs API
   - Google Sheets API
   - Google Forms API

4. **Name the app and make it Internal.**
   - Open **Google Auth Platform** (link in Handbook) and press **Get started**.
   - App name: **Handbook**. User support email: your email. Press **Next**.
   - **Audience:** choose **Internal**. Press **Next**.
   - Contact email: your email. Press **Next**, tick the agreement, and press **Create**.

5. **Create the sign-in key.**
   - In Google Auth Platform, open **Clients** and press **Create client**.
   - Application type: **Web application**. Name: **Handbook**.
   - Under **Authorized redirect URIs**, press **Add URI** and paste the first address from Handbook. Press **Add URI** again and paste the second one.
   - Press **Create**.

6. **Copy the keys into Handbook.** Google shows a **Client ID** and a **Client secret**. Paste both into Handbook and press **Save**.
   - Lost the window? In **Clients**, open **Handbook**. The Client ID is at the top. For the secret, press **Add secret** and copy the new one.

7. **Connect.** On the **Google Drive** card, press **Connect**, sign in with your company account, and pick the folder Handbook should read.

### If something goes wrong (Google)

| What you see | What to do |
| --- | --- |
| "That isn't a Google Client ID" when saving | You pasted something else into the first box. The Client ID ends in `.apps.googleusercontent.com`. |
| **Error 400: redirect_uri_mismatch** | One of the two addresses from step 5 is missing or has a typo. Copy both again from Handbook with the **Copy** button. Google can take up to 5 minutes to apply a change. |
| **Access blocked: can only be used within its organization** | Someone outside your company tried to connect. Use a company Google account. |
| **Internal** is greyed out in step 4 | Your account isn't part of a Google Workspace. Ask whoever manages your company's Google accounts to do the setup. |
| "Google Docs API has not been used in project…" | One of the four services in step 3 isn't turned on. Enable it, wait a few minutes, and try again. |

---

## Slack

**You need:** permission to install apps in your Slack workspace. Slack admins and owners have it.

1. **Open the setup in Handbook.** Go to **Sources**. Under **Your company's apps**, press **Set up** on the **Slack app** card.

2. **Create the app.** Press **Create the Slack app**. Slack opens with everything already filled in.
   - Pick your workspace and press **Next**, then **Create**.
   - If Slack shows an empty box instead, press **Slack shows an empty box instead?** in Handbook, copy the template, and paste it into Slack (choose **JSON** above the box).

3. **Ignore the first warning.** Slack may say the Request URL did not answer. That's expected: Handbook doesn't have the app's keys yet. If Slack offers commands to "run your app locally", ignore them and press **Go to App Settings**.

4. **Copy the keys into Handbook.** In Slack, open **Basic Information** and scroll to **App Credentials**. Copy each one into the matching box in Handbook (press **Show** in Slack to reveal a secret):
   - **Client ID**
   - **Client Secret**
   - **Signing Secret**

   Press **Save**.

5. **Confirm Slack can reach Handbook.** Back in Slack, open **Event Subscriptions** and press **Retry** next to the Request URL. It should say **Verified**.

6. **Connect.** On the **Slack** card in Handbook, press **Connect**, approve the app for your workspace, and pick the channels Handbook should read.

7. **Invite the bot** into any channel where people should be able to ask it questions: type `/invite @Handbook` in that channel.

### If something goes wrong (Slack)

| What you see | What to do |
| --- | --- |
| "That isn't a Slack Signing Secret" when saving | You pasted the Client Secret or a token. The Signing Secret is the third value under **App Credentials**. |
| **Retry** in step 5 still fails | Check the keys were saved in Handbook (the card says **Ready**), then press Retry again. |
| Slack asks which workspace to install to, and yours isn't listed | The app can only be installed in the workspace you created it in. Create it again from step 2 in the right workspace. |
| The bot doesn't answer in a channel | Invite it to that channel (step 7). The bot answers only people whose Slack email belongs to a Handbook account. |

---

## Connections made before the app

Google Drive or Slack connected before your company's app was set up shows **Set up your company's app first**. Set the app up, then press **Reconnect** on that card. Nothing already indexed is lost.

## Changing or replacing an app

You can paste new keys at any time with **Change keys** on the app's card. After saving, Google Drive or Slack shows **Reconnect**: press it once so the new app is used.

## Agent mode

Agent mode, which writes to Google Docs, Sheets and Slack for you, signs in through the same apps. There's nothing extra to set up: the addresses and permissions above already include it.
