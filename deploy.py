import os, json, shutil, uuid, re, time, subprocess, requests
from pathlib import Path
from msal import ConfidentialClientApplication

# --- Auth
client_id = os.environ["PBI_CLIENT_ID"]
client_secret = os.environ["PBI_CLIENT_SECRET"]
tenant_id = os.environ["PBI_TENANT_ID"]

# --- MSAL Auth
app = ConfidentialClientApplication(
    client_id = client_id,
    authority = f"https://login.microsoftonline.com/{tenant_id}",
    client_credential= client_secret
)

token_result = app.acquire_token_for_client(
    scopes = ["https://analysis.windows.net/powerbi/api/.default"]
)

access_token = token_result["access_token"]

# --- CONFIG ---
base_src_path = "LakehouseReport_src"
template_model_path = "models/TemplateModel"
output_path = "output"
temp_src_path = "temp_src"
workspace_id = "d66155d6-e646-423a-a72e-e556befa7890"
config_path = os.path.join(base_src_path, "deployment-configs.json")
runner_temp = os.environ.get("RUNNER_TEMP")
pbi_tools_path = os.path.join(runner_temp, "pbi-tools", "pbi-tools.core.exe")
# --- Install dotnet based pbi-tools.core.exe in Github Actions
#pbi_tools_path = r"C:\Users\YashParte\Downloads\pbiTool\pbi-tools.core.exe"
#pbidesktop_path = r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe"

os.makedirs(output_path, exist_ok=True)

with open(config_path) as f:
    configs = json.load(f)

for config in configs:
    warehouse_artifact_id = config["WarehouseArtifactID"]
    report_name = config["ReportName"]
    model_name = config["SemanticModel"]
    model_path = f"models/{model_name}"

    print(f"\n🔧 Processing: {report_name} with model {model_name}")

    # --- Clone template model folder ---
    if not os.path.exists(model_path):
        shutil.copytree(template_model_path, model_path)
        print(f"🧬 Model cloned to {model_path}")

    # --- DUPLICATION OF GUID
    # --- GUID REGISTRY ---

    # --- Update GUID and name ---
    db_file = os.path.join(model_path, "database.json")
    guid = str(uuid.uuid4())
    with open(db_file, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(r'"id":\s*".*?"', f'"id": "{guid}"', content, count=1)
    content = re.sub(r'"name":\s*".*?"', f'"name": "{model_name}"', content, count=1)
    with open(db_file, "w", encoding="utf-8") as f:
        f.write(content)

    # --- Update Lakehouse ID ---
    query_file = os.path.join(model_path, "expressions", "DatabaseQuery.json")
    with open(query_file, "r", encoding="utf-8") as f:
        content = f.read()
    # Build escaped replacement values
    pattern = r'(Sql\.Database\(\\"[^\\"]+\\",\s*\\")[^\\"]+(\\")'
    replacement = rf'\1{warehouse_artifact_id}\2'

    # Replace in the content
    content = re.sub(pattern, replacement, content)

    with open(query_file, "w", encoding="utf-8") as f:
        f.write(content)

    # --- Commit and push semantic model files to Git ---
    subprocess.run(["git", "add", model_path],check=True)
    subprocess.run(["git", "commit", "-m", f"Added {model_name} for {report_name}"],check=True)
    subprocess.run(["git","push"],check=True)

    # --- Delaying to let Fabric auto-sync with Git ---
    time.sleep(120)

    # --- Report processing ---
    if os.path.exists(temp_src_path):
        shutil.rmtree(temp_src_path)
    shutil.copytree(base_src_path, temp_src_path)

    # --- Version
    client_src_path = os.join.path("reports",f"{report_name}_src")

    # --- Update connections.json ---
    conn_file = os.path.join(temp_src_path, "Connections.json")
    with open(conn_file, "r", encoding="utf-8") as f:
        conn_data = f.read()

    # Replace Initial Catalog (guid)
    conn_data = re.sub(
        r'Initial Catalog=[^;"]+',
        f'Initial Catalog={guid}',
        conn_data
    )

    # Clear model/database/report IDs to avoid hard binding
    conn_data = re.sub(r'"PbiModelDatabaseName":\s*"[^"]+"', f'"PbiModelDatabaseName": "{guid}"', conn_data)
    conn_data = re.sub(r'"DatasetId":\s*"[^"]+"', f'"DatasetId": "{guid}"', conn_data)
    conn_data = re.sub(r'"ReportId":\s*"[^"]+"', '"ReportId": ""', conn_data)

    with open(conn_file, "w", encoding="utf-8") as f:
        f.write(conn_data)

    print(f"✅ Updated connections.json for {model_name}")

    if os.path.exists(client_src_path):
        shutil.rmtree(client_src_path)
    shutil.copytree(temp_src_path, client_src_path)
    print(f"Archived final source to {client_src_path}")

    # --- VALIDATE POST GIT-INTEGRATION
    # --- Compile .pbix ---
    #pbit_path = os.path.join(output_path, f"{report_name}.pbit")
    pbix_path = os.path.join(output_path, f"{report_name}.pbix")

    subprocess.run([pbi_tools_path, "compile", temp_src_path, pbix_path], check=True)
    print(f"✅ Compiled {report_name} to .pbix")

    with open(pbix_path, "rb") as f:
        pbix_binary = f.read()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream"
    }

    publish_url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/imports?datasetDisplayName={report_name}&nameConflict=Overwrite"
    resp = requests.post(publish_url, headers=headers, data=pbix_binary)

    if resp.status_code==200:
        print(f"Published: {report_name}")
    else:
        print(f"Failed to publish: {resp.status_code} - {resp.text}")
'''
    # --- Launch Power BI Desktop ---
    subprocess.Popen([pbidesktop_path, pbit_path])
    input("🕒 Refresh & Save .pbix manually. Press Enter when done...")

    if not os.path.exists(pbix_path) or os.path.getsize(pbix_path) == 0:
        print("❌ .pbix not saved or empty.")
        continue

    # --- Publish via REST API ---
    with open(pbix_path, "rb") as f:
        pbix_binary = f.read()

    token = subprocess.check_output(["PowerBI", "Get-PowerBIAccessToken"]).decode().strip()  # optional
    headers = {
        "Authorization": token,
        "Content-Type": "application/octet-stream"
    }

    publish_url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/imports?datasetDisplayName={report_name}&nameConflict=Overwrite"
    resp = requests.post(publish_url, headers=headers, data=pbix_binary)

    if resp.status_code == 200:
        print(f"✅ Published: {report_name}")
    else:
        print(f"❌ Failed to publish: {resp.text}")
'''
        
