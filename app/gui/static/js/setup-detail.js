import { currentSetupDetailInfo } from "./state.js";

export function renderSetupDetailJsonOutput(forceShow = false) {
  const output = document.getElementById("setup-detail-json-output");
  if (!output || !currentSetupDetailInfo) return;
  const visible = forceShow || !output.hidden;
  output.hidden = !visible;
  if (visible) {
    output.textContent = JSON.stringify(currentSetupDetailInfo, null, 2);
  }
}
