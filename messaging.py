import subprocess

from openai import OpenAI

MODEL = "gpt-5.4-mini"


def get_contact_identifiers(contact_name):
    """Return (primary_phone, [all_identifiers]) for a contact.
    all_identifiers contains every phone and email on the contact card —
    any of these can be the handle iMessage actually uses."""
    script = f'''
    tell application "Contacts"
        launch
        set thePeople to (people whose name is "{contact_name}")
        if (count of thePeople) > 0 then
            set thePerson to item 1 of thePeople
            set output to ""
            try
                set phoneList to value of phones of thePerson
                repeat with p in phoneList
                    set output to output & "P:" & p & linefeed
                end repeat
            end try
            try
                set emailList to value of emails of thePerson
                repeat with e in emailList
                    set output to output & "E:" & e & linefeed
                end repeat
            end try
            return output
        else
            return "NOT_FOUND"
        end if
    end tell
    '''
    result = subprocess.check_output(['osascript', '-e', script]).decode('utf-8').strip()
    if result == "NOT_FOUND" or not result:
        return None, []

    phones, emails = [], []
    for line in result.splitlines():
        line = line.strip()
        if line.startswith("P:"):
            phones.append(line[2:].strip())
        elif line.startswith("E:"):
            emails.append(line[2:].strip())
    primary_phone = phones[0] if phones else None
    return primary_phone, phones + emails


def clean_number(number):
    return ''.join(filter(lambda x: x.isdigit() or x == '+', number))


def suggest_reply(contact_name, history):
    if not history:
        raise ValueError("No prior messages found with this contact.")

    transcript = "\n".join(f"{sender.capitalize()}: {text}" for sender, text, _ in history)

    system_prompt = (
        f"Draft my next iMessage to {contact_name}. Match my tone and formality levels from prior messages sent by [Me]. Capitalize first word. Avoid emojis in most cases. "
        "Do not repeat or rephrase what I already said — move the conversation forward "
        "with new content (answer a question they asked, react to their last message, "
        "or add a new thought). "
        "Reply with only the message text — no quotes, no preface."
    )
    user_prompt = (
        f"Conversation (Me = me, Them = {contact_name}):\n\n{transcript}"
    )

    print("--- Sending to OpenAI ---")
    print(f"[system]\n{system_prompt}\n")
    print(f"[user]\n{user_prompt}")
    print("--- End ---\n")

    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content.strip().strip('"').strip("'")


def send_imessage(clean_num, message):
    # Escape for AppleScript string literal: backslashes first, then double quotes.
    escaped = message.replace('\\', '\\\\').replace('"', '\\"')

    def try_send(service_type):
        script = f'''
        tell application "Messages"
            set targetService to 1st service whose service type = {service_type}
            set targetBuddy to buddy "{clean_num}" of targetService
            send "{escaped}" to targetBuddy
        end tell
        '''
        subprocess.run(
            ['osascript', '-e', script],
            check=True, capture_output=True,
        )

    # Try iMessage first (blue), fall back to SMS (green) if the contact isn't on iMessage.
    try:
        try_send("iMessage")
        print(f"Sent via iMessage to {clean_num}.")
        return
    except subprocess.CalledProcessError:
        pass
    try:
        try_send("SMS")
        print(f"Sent via SMS to {clean_num}.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to send message to {clean_num}: {e.stderr.decode('utf-8', errors='replace').strip()}")
