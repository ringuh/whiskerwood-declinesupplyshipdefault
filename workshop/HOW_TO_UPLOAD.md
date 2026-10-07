# Uploading DeclineSupplyShipDefault to the Steam Workshop

Whiskerwood has no in-game uploader; the scripts use Valve's SteamCMD (`E:\modding\steamcmd\steamcmd.exe`).

1. In the modkit, run **Cook & Install** on `Content/Mods/DeclineSupplyShipDefault`.
2. Double-click **`sync-from-modkit.bat`** in the repo root. It copies the assets into `Mod/DeclineSupplyShipDefault/`
   and stages `DeclineSupplyShipDefault.pak` + `DeclineSupplyShipDefault.uplugin` into `workshop/content/` (git-ignored).
3. Update `"changenote"` in `DeclineSupplyShipDefault.vdf` (and `"Version"` in `Mod/DeclineSupplyShipDefault/DeclineSupplyShipDefault.uplugin`).
4. Double-click **`sync-to-steam.bat`**. It logs in as `pienirinkula` (SteamCMD asks for the password /
   Steam Guard code if it has no saved login) and uploads with `workshop_build_item`.
5. The first upload writes the new item id into `"publishedfileid"` in the .vdf - commit that change,
   and check the item's visibility on its Workshop page. Later updates: same steps, new changenote.

The preview image is `docs/screenshot.png`.
