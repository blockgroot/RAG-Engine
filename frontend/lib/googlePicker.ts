/**
 * Google's own folder picker. The folder is the person's click in Google's
 * window, which is also what lets Handbook's narrow drive.file access reach
 * it: nothing else in their Drive becomes visible.
 */

type PickerConfig = { api_key: string; app_id: string; token: string };

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Google = any;

let loading: Promise<Google> | null = null;

function loadPicker(): Promise<Google> {
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://apis.google.com/js/api.js";
    script.async = true;
    script.onload = () => {
      const w = window as unknown as { gapi: Google; google: Google };
      w.gapi.load("picker", { callback: () => resolve(w.google), onerror: reject });
    };
    script.onerror = () => {
      loading = null;
      reject(new Error("Could not load Google's folder picker."));
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** Resolves with the picked folder's id, or null if they closed the window. */
export async function pickFolder(config: PickerConfig): Promise<string | null> {
  const google = await loadPicker();
  return new Promise((resolve) => {
    const view = new google.picker.DocsView(google.picker.ViewId.FOLDERS)
      .setIncludeFolders(true)
      .setSelectFolderEnabled(true)
      .setMimeTypes("application/vnd.google-apps.folder");
    new google.picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(config.token)
      .setDeveloperKey(config.api_key)
      .setAppId(config.app_id)
      .setTitle("Choose a folder for the new file")
      .setCallback((data: Google) => {
        if (data.action === google.picker.Action.PICKED) resolve(data.docs?.[0]?.id ?? null);
        else if (data.action === google.picker.Action.CANCEL) resolve(null);
      })
      .build()
      .setVisible(true);
  });
}
