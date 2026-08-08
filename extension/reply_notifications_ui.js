import {loadSettings, saveSettings} from "./lib.js";

const checkbox = document.getElementById("notifyReplies");
if (checkbox) {
  const settings = await loadSettings();
  checkbox.checked = settings.notifyOnSessionComplete !== false;
  checkbox.addEventListener("change", async () => {
    const current = await loadSettings();
    current.notifyOnSessionComplete = checkbox.checked;
    await saveSettings(current);
  });
}
