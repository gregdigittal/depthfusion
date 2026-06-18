import { invoke } from '@tauri-apps/api/core'
import { relaunch } from '@tauri-apps/plugin-process'
import { check } from '@tauri-apps/plugin-updater'

export type UpdateChannel = 'stable' | 'beta'

export interface UpdateInfo {
  version: string
  currentVersion: string
  body: string | null
  date: string | null
}

/**
 * Check for a pending update via the Tauri IPC command.
 * Returns UpdateInfo if a newer version is available, null otherwise.
 */
export async function checkForUpdate(): Promise<UpdateInfo | null> {
  return invoke<UpdateInfo | null>('check_update')
}

/**
 * Download and install the available update, then relaunch the app.
 * Calls the JS-side updater plugin for download progress support.
 * @param onProgress Optional callback with bytes downloaded and total bytes.
 */
export async function applyUpdate(
  onProgress?: (downloaded: number, total: number | null) => void,
): Promise<void> {
  const update = await check()

  if (!update) {
    throw new Error('No update available to apply')
  }

  let downloaded = 0
  let total: number | null = null

  await update.downloadAndInstall((event) => {
    switch (event.event) {
      case 'Started':
        total = event.data.contentLength ?? null
        break
      case 'Progress':
        downloaded += event.data.chunkLength
        onProgress?.(downloaded, total)
        break
      case 'Finished':
        break
    }
  })

  await relaunch()
}

/**
 * Derive the update endpoint URL for the given channel.
 * The base URL is baked into tauri.conf.json; this helper documents
 * how to select stable vs beta by appending a query param if desired.
 *
 * In practice the channel is controlled server-side via the endpoint
 * configured in tauri.conf.json. This function is provided so the UI
 * can surface the active channel to the user.
 */
export function getUpdateChannel(): UpdateChannel {
  // Read channel from localStorage so it persists across relaunches.
  const stored = localStorage.getItem('depthfusion.updateChannel')
  if (stored === 'beta') return 'beta'
  return 'stable'
}

export function setUpdateChannel(channel: UpdateChannel): void {
  localStorage.setItem('depthfusion.updateChannel', channel)
}
