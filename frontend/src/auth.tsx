import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, get, setAccessToken, setSessionLostHandler, tryResume } from "./api";
import type { ModuleMeta, Profile } from "./types";

type AuthState = {
  profile: Profile | null;
  modules: ModuleMeta[];
  loading: boolean;
  can: (perm: string) => boolean;
  signedIn: (accessToken: string) => Promise<void>;
  logout: () => Promise<void>;
  reloadMeta: () => Promise<void>;
};

const Ctx = createContext<AuthState>(null as unknown as AuthState);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [modules, setModules] = useState<ModuleMeta[]>([]);
  const [loading, setLoading] = useState(true);

  const reloadMeta = useCallback(async () => {
    const meta = await get<{ modules: ModuleMeta[] }>("/api/meta");
    setModules(meta.modules);
  }, []);

  const load = useCallback(async () => {
    const me = await get<Profile>("/api/auth/me");
    setProfile(me);
    await reloadMeta();
  }, [reloadMeta]);

  useEffect(() => {
    setSessionLostHandler(() => {
      setProfile(null);
      setModules([]);
    });
    tryResume()
      .then(async (me) => {
        if (me) {
          setProfile(me);
          await reloadMeta();
        }
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [reloadMeta]);

  const value = useMemo<AuthState>(() => ({
    profile,
    modules,
    loading,
    can: (perm) => !!profile?.permissions.includes(perm),
    signedIn: async (token) => {
      setAccessToken(token);
      await load();
    },
    logout: async () => {
      try {
        await api("POST", "/api/auth/logout");
      } finally {
        setAccessToken(null);
        setProfile(null);
        setModules([]);
      }
    },
    reloadMeta,
  }), [profile, modules, loading, load, reloadMeta]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
