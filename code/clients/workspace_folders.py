"""
Workspace **folders** — the Fabric UI's item-organisation tree.

API reference: https://learn.microsoft.com/en-us/rest/api/fabric/core/folders

Not to be confused with the *landing-zone* folders in `landing_zone.py`: those are
OneLake directories that carry replication semantics (a table folder's name is the
table name). These are pure workspace organisation — moving an item between them
changes nothing about the item except where the UI files it.

Operations (SDK groups behind the seam):
  - List / Create / Get / Delete folders:  ``fabric_client().core.folders``
  - Rename an item:                        ``fabric_client().core.items.update_item``
  - Move an item into a folder:            ``fabric_client().core.items.move_item``
                                           (omit target → workspace root)

Hard-won notes (one line each; details in ``3_wiki/cicd/coding-guidance.md``):
  - Renaming an item does not move its data — landing zones are addressed by item GUID.
  - Child items follow their parent — a SQLEndpoint is renamed/moved by the platform, never
    directly.
  - Folder display names are more restricted than item names (no `" / \\ : | < > * ?`, no
    leading/trailing space, not `.`/`..`).

Errors surface as ``azure.core.exceptions.HttpResponseError`` (carries ``.status_code``
and the Fabric error body) rather than ``requests.HTTPError``.
"""
from config import FABRIC_WORKSPACE_ID
from sdk import fabric_client, sdk_models, to_wire


class FolderClient:

    def __init__(self, workspace_id: str = FABRIC_WORKSPACE_ID, *, sdk=None):
        self.workspace_id = workspace_id
        self._sdk = sdk

    @property
    def _api(self):
        if self._sdk is None:
            self._sdk = fabric_client()
        return self._sdk

    # ── folders ─────────────────────────────────────────────────────

    def list_folders(self) -> list[dict]:
        """Every folder in the workspace, flat. Nesting is via ``parentFolderId``."""
        return to_wire(self._api.core.folders.list_folders(self.workspace_id))

    def resolve_by_name(self, display_name: str,
                        parent_folder_id: str | None = None) -> dict | None:
        """Find a folder by exact display name (None if absent).

        ``parent_folder_id`` disambiguates same-named folders at different depths;
        None matches a top-level folder.
        """
        return next((f for f in self.list_folders()
                     if f.get("displayName") == display_name
                     and f.get("parentFolderId") == parent_folder_id), None)

    def create_folder(self, display_name: str,
                      parent_folder_id: str | None = None) -> dict:
        body: dict = {"displayName": display_name}
        if parent_folder_id:
            body["parentFolderId"] = parent_folder_id
        request = sdk_models("core").CreateFolderRequest.from_dict(body)
        return to_wire(self._api.core.folders.create_folder(self.workspace_id, request))

    def ensure_folder(self, display_name: str,
                      parent_folder_id: str | None = None) -> str:
        """Folder id, creating the folder if it does not exist. Idempotent — safe to
        call at the top of every run."""
        existing = self.resolve_by_name(display_name, parent_folder_id)
        if existing:
            return existing["id"]
        return self.create_folder(display_name, parent_folder_id)["id"]

    def delete_folder(self, folder_id: str) -> int:
        return self._api.core.folders.delete_folder(
            self.workspace_id, folder_id,
            cls=lambda pipeline_response, _, __: pipeline_response.http_response.status_code)

    # ── items within folders ────────────────────────────────────────

    def list_items(self, folder_id: str | None = None) -> list[dict]:
        """Workspace items, optionally only those directly in ``folder_id``."""
        results = to_wire(self._api.core.items.list_items(self.workspace_id))
        if folder_id is not None:
            results = [i for i in results if i.get("folderId") == folder_id]
        return results

    def rename_item(self, item_id: str, display_name: str) -> dict:
        """Rename any item in place. Data, GUID and landing zone are unaffected."""
        request = sdk_models("core").UpdateItemRequest.from_dict(
            {"displayName": display_name})
        return to_wire(self._api.core.items.update_item(self.workspace_id, item_id, request))

    def move_item(self, item_id: str, target_folder_id: str | None = None) -> dict:
        """Move an item into a folder (None → workspace root)."""
        body = {"targetFolderId": target_folder_id} if target_folder_id else {}
        request = sdk_models("core").MoveItemRequest.from_dict(body)
        return to_wire(self._api.core.items.move_item(self.workspace_id, item_id, request))

    def place_item(self, item_id: str, display_name: str,
                   folder_id: str | None = None) -> dict:
        """Rename and file an item in one call. Returns the updated item."""
        self.rename_item(item_id, display_name)
        return self.move_item(item_id, folder_id)


if __name__ == "__main__":
    client = FolderClient()
    folders = client.list_folders()
    by_id = {f["id"]: f["displayName"] for f in folders}
    print("=== Folders ===")
    for f in sorted(folders, key=lambda x: (by_id.get(x.get("parentFolderId"), ""),
                                            x["displayName"])):
        parent = by_id.get(f.get("parentFolderId"))
        print(f"  {f['displayName']:24} id={f['id']}" + (f"  (in {parent})" if parent else ""))
    print("\n=== Items ===")
    for i in sorted(client.list_items(), key=lambda x: (x.get("type", ""),
                                                        x.get("displayName", ""))):
        print(f"  {i.get('type'):20} {i.get('displayName'):34} "
              f"folder={by_id.get(i.get('folderId'), '(root)')}")
