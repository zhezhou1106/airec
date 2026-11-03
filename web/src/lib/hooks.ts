import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { ConfigData, DirectionList, Overview, WeekReport, WeekSummary } from "./types";

export const useOverview = () =>
  useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/overview"), refetchInterval: 60_000 });

export const useWeeks = () =>
  useQuery({ queryKey: ["weeks"], queryFn: () => api<{ weeks: WeekSummary[] }>("/api/weeks"), refetchInterval: 60_000 });

export const useWeek = (week: string | undefined) =>
  useQuery({ queryKey: ["week", week], queryFn: () => api<WeekReport>(`/api/weeks/${week}`), enabled: !!week });

export const useDirections = () =>
  useQuery({ queryKey: ["directions"], queryFn: () => api<DirectionList>("/api/directions") });

export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: () => api<ConfigData>("/api/config") });
