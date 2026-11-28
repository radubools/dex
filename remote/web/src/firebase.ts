// The one copy of Firebase, owned by the public shell and handed to the private
// application bundle when it mounts (`AppContext.fb`). The application imports
// Firebase only for its types, so it carries no second copy and shares this
// signed-in session.
import { initializeApp } from 'firebase/app'
import {
  GoogleAuthProvider, getAuth, onAuthStateChanged, signInWithPopup, signOut,
} from 'firebase/auth'
import {
  collection, doc, getDoc, getDocs, getFirestore, limit, orderBy, query, where,
} from 'firebase/firestore'
import { getBlob, getMetadata, getStorage, ref, uploadBytesResumable } from 'firebase/storage'
import type { User } from 'firebase/auth'

export type RemoteConfig = {
  firebase: Record<string, string>
  /** projects/<id>/topics/<name> */
  topic: string
  pollSeconds?: number
}

/** The signed-in person's users/<email> entry, kept by hand in Firebase. */
export type Profile = { role: 'user' | 'admin'; projects: Record<string, boolean>; name?: string }

export function connect(config: RemoteConfig) {
  const app = initializeApp(config.firebase)
  return {
    auth: getAuth(app),
    db: getFirestore(app),
    storage: getStorage(app),
    a: { GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged },
    f: { collection, doc, getDoc, getDocs, limit, orderBy, query, where },
    s: { getBlob, getMetadata, ref, uploadBytesResumable },
  }
}

export type Fb = ReturnType<typeof connect>

export type AppContext = {
  config: RemoteConfig
  user: User
  profile: Profile
  fb: Fb
  /** Sign in again for a fresh Pub/Sub token; the shell owns sign-in. */
  pubsubToken: () => Promise<string>
}
