export async function copySetupTemplateToClipboard(template) {
  const text = JSON.stringify(template, null, 2);
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (error) {
    // Fall back to the legacy copy path below.
  }
  return fallbackCopyTextToClipboard(text);
}

export function fallbackCopyTextToClipboard(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.top = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (error) {
    copied = false;
  }
  document.body.removeChild(textarea);
  return copied;
}

export async function copySetupDetailInfoToClipboard(infoPromise) {
  try {
    if (navigator.clipboard && window.isSecureContext && window.ClipboardItem) {
      const blobPromise = infoPromise.then(
        (info) => new Blob([JSON.stringify(info, null, 2)], { type: "text/plain" }),
      );
      await navigator.clipboard.write([new ClipboardItem({ "text/plain": blobPromise })]);
      return true;
    }
  } catch (error) {
    // Fall back to the legacy copy path below once the data resolves.
  }
  return copySetupTemplateToClipboard(await infoPromise);
}
