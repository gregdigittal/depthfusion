/**
 * connectivity.ts — Connectivity state machine + write queue.
 *
 * States: ONLINE, OFFLINE, RECONNECTING, SYNCING
 *
 * When OFFLINE: write operations are queued in IndexedDB (key: timestamp, value: operation JSON).
 * When ONLINE restored: queued operations are flushed to the server via sync push, in order.
 *
 * React integration: ConnectivityContext + useConnectivity() hook.
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useRef,
  createElement,
  type ReactNode,
} from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ConnectivityState = 'ONLINE' | 'OFFLINE' | 'RECONNECTING' | 'SYNCING'

export interface QueuedOperation {
  /** Unique key: ISO timestamp at enqueue time */
  timestamp: string
  /** Serialised operation payload */
  operation: unknown
}

export interface ConnectivityStatus {
  state: ConnectivityState
  queuedCount: number
  lastOnlineAt: string | null
  lastError: string | null
}

export interface ConnectivityContextValue {
  status: ConnectivityStatus
  /** Enqueue a write operation for delivery when online */
  enqueue: (operation: unknown) => Promise<void>
  /** Force a flush of the write queue (no-op when OFFLINE) */
  flush: () => Promise<void>
}

// ---------------------------------------------------------------------------
// IndexedDB write queue
// ---------------------------------------------------------------------------

const DB_NAME = 'depthfusion-connectivity'
const DB_VERSION = 1
const STORE_NAME = 'write-queue'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        // timestamp is the keyPath — guarantees ordering
        db.createObjectStore(STORE_NAME, { keyPath: 'timestamp' })
      }
    }

    request.onsuccess = (event) => {
      resolve((event.target as IDBOpenDBRequest).result)
    }

    request.onerror = (event) => {
      reject(new Error(`IndexedDB open failed: ${(event.target as IDBOpenDBRequest).error?.message ?? 'unknown'}`))
    }
  })
}

async function dbEnqueue(db: IDBDatabase, entry: QueuedOperation): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const req = store.put(entry)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(new Error(`enqueue failed: ${req.error?.message ?? 'unknown'}`))
  })
}

async function dbGetAll(db: IDBDatabase): Promise<QueuedOperation[]> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const req = store.getAll()
    req.onsuccess = () => resolve((req.result as QueuedOperation[]).sort((a, b) => a.timestamp.localeCompare(b.timestamp)))
    req.onerror = () => reject(new Error(`getAll failed: ${req.error?.message ?? 'unknown'}`))
  })
}

async function dbDelete(db: IDBDatabase, timestamp: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const req = store.delete(timestamp)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(new Error(`delete failed: ${req.error?.message ?? 'unknown'}`))
  })
}

async function dbCount(db: IDBDatabase): Promise<number> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly')
    const store = tx.objectStore(STORE_NAME)
    const req = store.count()
    req.onsuccess = () => resolve(req.result as number)
    req.onerror = () => reject(new Error(`count failed: ${req.error?.message ?? 'unknown'}`))
  })
}

// ---------------------------------------------------------------------------
// Sync push endpoint
// ---------------------------------------------------------------------------

const SYNC_ENDPOINT = '/api/v2/sync/push'

async function syncPush(operations: QueuedOperation[]): Promise<void> {
  if (operations.length === 0) return

  const response = await fetch(SYNC_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operations }),
  })

  if (!response.ok) {
    throw new Error(`sync push failed: ${response.status} ${response.statusText}`)
  }
}

// ---------------------------------------------------------------------------
// Connectivity state machine
// ---------------------------------------------------------------------------

type StateTransition =
  | { type: 'WENT_OFFLINE' }
  | { type: 'WENT_ONLINE' }
  | { type: 'SYNC_STARTED' }
  | { type: 'SYNC_COMPLETE' }
  | { type: 'SYNC_FAILED'; error: string }
  | { type: 'QUEUE_UPDATED'; count: number }

function reduce(prev: ConnectivityStatus, action: StateTransition): ConnectivityStatus {
  switch (action.type) {
    case 'WENT_OFFLINE':
      return { ...prev, state: 'OFFLINE' }

    case 'WENT_ONLINE':
      // If there are queued items we'll move to RECONNECTING until the flush
      // starts; the flush then moves us to SYNCING → ONLINE.
      return {
        ...prev,
        state: prev.queuedCount > 0 ? 'RECONNECTING' : 'ONLINE',
        lastOnlineAt: new Date().toISOString(),
        lastError: null,
      }

    case 'SYNC_STARTED':
      return { ...prev, state: 'SYNCING', lastError: null }

    case 'SYNC_COMPLETE':
      return { ...prev, state: 'ONLINE', queuedCount: 0, lastError: null }

    case 'SYNC_FAILED':
      return { ...prev, state: 'OFFLINE', lastError: action.error }

    case 'QUEUE_UPDATED':
      return { ...prev, queuedCount: action.count }

    default:
      return prev
  }
}

// ---------------------------------------------------------------------------
// ConnectivityManager (stateful, framework-agnostic)
// ---------------------------------------------------------------------------

type Listener = (status: ConnectivityStatus) => void

class ConnectivityManager {
  private _status: ConnectivityStatus = {
    state: navigator.onLine ? 'ONLINE' : 'OFFLINE',
    queuedCount: 0,
    lastOnlineAt: navigator.onLine ? new Date().toISOString() : null,
    lastError: null,
  }

  private _db: IDBDatabase | null = null
  private _listeners = new Set<Listener>()
  private _flushing = false

  constructor() {
    window.addEventListener('online', this._handleOnline)
    window.addEventListener('offline', this._handleOffline)
    this._init().catch((err: unknown) => {
      console.error('[ConnectivityManager] init failed', err)
    })
  }

  // ---- Public API --------------------------------------------------------

  subscribe(fn: Listener): () => void {
    this._listeners.add(fn)
    fn(this._status)
    return () => this._listeners.delete(fn)
  }

  getStatus(): ConnectivityStatus {
    return this._status
  }

  async enqueue(operation: unknown): Promise<void> {
    const db = await this._getDb()
    const entry: QueuedOperation = {
      timestamp: new Date().toISOString() + '_' + Math.random().toString(36).slice(2),
      operation,
    }
    await dbEnqueue(db, entry)
    const count = await dbCount(db)
    this._dispatch({ type: 'QUEUE_UPDATED', count })
  }

  async flush(): Promise<void> {
    if (this._flushing) return
    const db = await this._getDb()
    const ops = await dbGetAll(db)
    if (ops.length === 0) {
      this._dispatch({ type: 'SYNC_COMPLETE' })
      return
    }

    this._flushing = true
    this._dispatch({ type: 'SYNC_STARTED' })

    // Push in-order, one-by-one so we can remove each on success
    try {
      for (const op of ops) {
        await syncPush([op])
        await dbDelete(db, op.timestamp)
      }
      this._flushing = false
      this._dispatch({ type: 'SYNC_COMPLETE' })
    } catch (err: unknown) {
      this._flushing = false
      const msg = err instanceof Error ? err.message : String(err)
      this._dispatch({ type: 'SYNC_FAILED', error: msg })
      throw err
    }
  }

  destroy(): void {
    window.removeEventListener('online', this._handleOnline)
    window.removeEventListener('offline', this._handleOffline)
    this._db?.close()
    this._db = null
    this._listeners.clear()
  }

  // ---- Private -----------------------------------------------------------

  private async _init(): Promise<void> {
    const db = await this._getDb()
    const count = await dbCount(db)
    if (count > 0) {
      this._dispatch({ type: 'QUEUE_UPDATED', count })
    }
    // If we start online with a non-empty queue, kick off a flush
    if (this._status.state === 'ONLINE' && count > 0) {
      this._dispatch({ type: 'WENT_ONLINE' })
      this.flush().catch((err: unknown) => {
        console.error('[ConnectivityManager] initial flush failed', err)
      })
    }
  }

  private async _getDb(): Promise<IDBDatabase> {
    if (!this._db) {
      this._db = await openDb()
    }
    return this._db
  }

  private _dispatch(action: StateTransition): void {
    this._status = reduce(this._status, action)
    this._listeners.forEach((fn) => fn(this._status))
  }

  private _handleOnline = (): void => {
    this._dispatch({ type: 'WENT_ONLINE' })
    if (this._status.queuedCount > 0) {
      this.flush().catch((err: unknown) => {
        console.error('[ConnectivityManager] flush on reconnect failed', err)
      })
    }
  }

  private _handleOffline = (): void => {
    this._dispatch({ type: 'WENT_OFFLINE' })
  }
}

// Singleton — one manager per renderer process
let _manager: ConnectivityManager | null = null

function getManager(): ConnectivityManager {
  if (!_manager) {
    _manager = new ConnectivityManager()
  }
  return _manager
}

// ---------------------------------------------------------------------------
// React context
// ---------------------------------------------------------------------------

const ConnectivityContext = createContext<ConnectivityContextValue | null>(null)

interface ConnectivityProviderProps {
  children: ReactNode
}

function ConnectivityProvider({ children }: ConnectivityProviderProps) {
  const manager = getManager()
  const [status, setStatus] = useState<ConnectivityStatus>(manager.getStatus())

  // Flush ref so the callbacks are stable
  const managerRef = useRef(manager)
  managerRef.current = manager

  useEffect(() => {
    return manager.subscribe(setStatus)
  }, [manager])

  const enqueue = useCallback(async (operation: unknown): Promise<void> => {
    await managerRef.current.enqueue(operation)
  }, [])

  const flush = useCallback(async (): Promise<void> => {
    await managerRef.current.flush()
  }, [])

  const value: ConnectivityContextValue = { status, enqueue, flush }

  return createElement(ConnectivityContext.Provider, { value }, children)
}

function useConnectivity(): ConnectivityContextValue {
  const ctx = useContext(ConnectivityContext)
  if (!ctx) {
    throw new Error('useConnectivity must be used within a ConnectivityProvider')
  }
  return ctx
}

export { ConnectivityProvider, ConnectivityContext, useConnectivity }
