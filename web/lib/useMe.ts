"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, ApiError, type User } from "./api";

/** Loads the current user; redirects to /login when there is no session. */
export function useMe({ redirect = true } = {}) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api<User>("/auth/me")
      .then(setUser)
      .catch((e) => {
        if (redirect && e instanceof ApiError && e.status === 401) router.replace("/login");
      })
      .finally(() => setLoading(false));
  }, [redirect, router]);

  return { user, setUser, loading };
}
