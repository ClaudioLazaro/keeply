"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Card,
  Select,
  SelectItem,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  TextInput,
} from "@tremor/react";
import { useAiopsInvestigations } from "@/entities/aiops/model/useAiops";
import { InvestigationStatus } from "@/entities/investigation/model/types";
import {
  EmptyState,
  ErrorState,
  InvestigationStatusBadge,
  LoadingState,
  PageHeader,
  formatAge,
  formatDateTime,
} from "./shared";

const STATUS_FILTERS: (InvestigationStatus | "all")[] = [
  "all",
  "queued",
  "gathering",
  "hypothesizing",
  "rca_ready",
  "failed",
  "cancelled",
];

export function InvestigationsClient() {
  const { investigations, isLoading, error } = useAiopsInvestigations();
  const [status, setStatus] = useState<string>("all");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (!investigations) return [];
    const needle = query.trim().toLowerCase();
    return investigations.filter((item) => {
      if (status !== "all" && item.status !== status) return false;
      if (!needle) return true;
      return (
        item.incident_id.toLowerCase().includes(needle) ||
        item.id.toLowerCase().includes(needle)
      );
    });
  }, [investigations, status, query]);

  if (error) return <ErrorState what="investigations" />;
  if (isLoading) return <LoadingState what="investigations" />;

  return (
    <div>
      <PageHeader
        title="Investigations"
        description="Every AI investigation, newest first. Suggest-only — no action is ever taken automatically."
      />

      <div className="flex flex-col sm:flex-row gap-2 mb-3">
        <TextInput
          placeholder="Filter by incident or investigation id…"
          value={query}
          onValueChange={setQuery}
          className="sm:max-w-md"
        />
        <Select
          value={status}
          onValueChange={setStatus}
          className="sm:max-w-[220px]"
          enableClear={false}
        >
          {STATUS_FILTERS.map((option) => (
            <SelectItem key={option} value={option}>
              {option === "all" ? "All statuses" : option}
            </SelectItem>
          ))}
        </Select>
      </div>

      {investigations && investigations.length === 0 ? (
        <EmptyState
          message="No investigations yet. One is created automatically when a critical or high-severity incident arrives."
        />
      ) : filtered.length === 0 ? (
        <EmptyState message="No investigations match this filter." />
      ) : (
        <Card className="!p-0 overflow-x-auto">
          <Table>
            <TableHead>
              <TableRow>
                <TableHeaderCell>Status</TableHeaderCell>
                <TableHeaderCell>Incident</TableHeaderCell>
                <TableHeaderCell>Age</TableHeaderCell>
                <TableHeaderCell>Created</TableHeaderCell>
                <TableHeaderCell>Detail</TableHeaderCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>
                    <InvestigationStatusBadge status={item.status} />
                    {item.status === "failed" && item.error && (
                      <p
                        className="text-xs text-red-600 mt-1 max-w-xs truncate"
                        title={item.error}
                      >
                        {item.error}
                      </p>
                    )}
                  </TableCell>
                  <TableCell>
                    <Link
                      href={`/incidents/${item.incident_id}`}
                      className="font-mono text-xs text-orange-600 hover:underline"
                    >
                      {item.incident_id.slice(0, 8)}
                    </Link>
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {formatAge(item.created_at)}
                  </TableCell>
                  <TableCell className="text-xs">
                    {formatDateTime(item.created_at)}
                  </TableCell>
                  <TableCell>
                    <Link
                      href={`/incidents/${item.incident_id}`}
                      className="text-xs text-orange-600 hover:underline"
                    >
                      Open incident →
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
