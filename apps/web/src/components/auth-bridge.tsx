"use client";

/**
 * Wires Clerk's getToken() into our api() helper so every dashboard request
 * carries a fresh Bearer JWT. Outside of Clerk (or while signed-out) we fall
 * back to the dev X-Dev-Org header.
 */
import { useAuth } from "@clerk/nextjs";
import { useEffect } from "react";
import { setAuthTokenProvider } from "@/lib/api";

export function AuthBridge() {
  const { getToken, isSignedIn } = useAuth();
  useEffect(() => {
    if (isSignedIn) {
      setAuthTokenProvider(() => getToken({ template: "default" }).catch(() => null));
    } else {
      setAuthTokenProvider(null);
    }
    return () => setAuthTokenProvider(null);
  }, [getToken, isSignedIn]);
  return null;
}
