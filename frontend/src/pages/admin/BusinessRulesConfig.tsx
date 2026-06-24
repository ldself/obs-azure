// Business Rules Configuration screen (Build Plan v1.2 §4.3).
// Four MUI tabs — one per business rules table. Edit affordances are shown only
// to System Modelers and Administrators; all other roles see read-only grids.
// The server enforces authorization (RULE 3 / RULE 5); the capability check here
// is display-layer convenience only.

import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { DataGrid, type GridColDef, type GridRenderCellParams } from "@mui/x-data-grid";

import { ApiError } from "../../api/client";
import {
  createOverheadRate,
  deactivateOverheadRate,
  listBurdenRates,
  listComponentMappings,
  listMeritRates,
  listOverheadRates,
  updateComponentMapping,
  updateOverheadRate,
  upsertBurdenRate,
  upsertMeritRate,
} from "../../api/businessRules";
import { useAuth } from "../../auth/AuthContext";
import type {
  CompensationBurdenRate,
  CompensationBurdenRateUpsert,
  CompensationComponentMapping,
  MeritIncreaseRate,
  MeritIncreaseRateUpsert,
  OverheadAllocationRate,
  OverheadAllocationRateCreate,
} from "../../types";

// ===== shared helpers =======================================================

function canWrite(user: { is_administrator: boolean; is_system_modeler: boolean } | null): boolean {
  return Boolean(user?.is_administrator || user?.is_system_modeler);
}

function formatRate(val: string): string {
  return `${parseFloat(val).toFixed(2)}%`;
}

function formatCurrency(val: string): string {
  return `$${parseFloat(val).toLocaleString("en-US", { minimumFractionDigits: 2 })}`;
}

// ===== Tab 1 — Merit Increase Rates =========================================

function MeritRatesTab({ writeAllowed }: { writeAllowed: boolean }): JSX.Element {
  const [rows, setRows] = useState<MeritIncreaseRate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<MeritIncreaseRate | null>(null);
  const [form, setForm] = useState<MeritIncreaseRateUpsert>({ rate_pct: "", effective_date: "" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listMeritRates());
      setError(null);
    } catch {
      setError("Failed to load merit increase rates.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const openEdit = (row: MeritIncreaseRate): void => {
    setEditing(row);
    setForm({ rate_pct: row.rate_pct, effective_date: row.effective_date });
    setSaveError(null);
  };

  const handleSave = async (): Promise<void> => {
    if (!editing) return;
    setSaving(true);
    setSaveError(null);
    try {
      await upsertMeritRate(editing.fiscal_year, form);
      setEditing(null);
      await load();
    } catch (err) {
      setSaveError(err instanceof ApiError ? String(err.detail) : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const columns: GridColDef[] = [
    { field: "fiscal_year", headerName: "Fiscal Year", width: 120 },
    {
      field: "rate_pct",
      headerName: "Merit Rate",
      width: 120,
      renderCell: (p: GridRenderCellParams) => formatRate(String(p.value)),
    },
    { field: "effective_date", headerName: "Effective Date", width: 140 },
    { field: "updated_by", headerName: "Last Updated By", flex: 1 },
    ...(writeAllowed
      ? [{
          field: "_actions",
          headerName: "",
          width: 80,
          sortable: false,
          renderCell: (p: GridRenderCellParams<MeritIncreaseRate>) => (
            <Button size="small" onClick={() => openEdit(p.row as MeritIncreaseRate)}>Edit</Button>
          ),
        }]
      : []),
  ];

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <DataGrid
        rows={rows}
        columns={columns}
        getRowId={(r: MeritIncreaseRate) => r.fiscal_year}
        loading={loading}
        autoHeight
        pageSizeOptions={[10, 25]}
        initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
      />
      <Dialog open={Boolean(editing)} onClose={() => setEditing(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Edit Merit Increase Rate — FY {editing?.fiscal_year}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {saveError && <Alert severity="error">{saveError}</Alert>}
            <TextField
              label="Merit Rate (%)"
              value={form.rate_pct}
              onChange={(e) => setForm((f) => ({ ...f, rate_pct: e.target.value }))}
              helperText="e.g. 3.0000 = 3%"
              fullWidth
            />
            <TextField
              label="Effective Date"
              type="date"
              value={form.effective_date}
              onChange={(e) => setForm((f) => ({ ...f, effective_date: e.target.value }))}
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button onClick={() => { void handleSave(); }} variant="contained" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

// ===== Tab 2 — Compensation Burden Rates ====================================

function BurdenRatesTab({ writeAllowed }: { writeAllowed: boolean }): JSX.Element {
  const [rows, setRows] = useState<CompensationBurdenRate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<CompensationBurdenRate | null>(null);
  const [form, setForm] = useState<CompensationBurdenRateUpsert>({
    fica_rate_pct: "", fica_wage_cap: "", medicare_rate_pct: "",
    state_income_tax_rate_pct: "", federal_income_tax_rate_pct: "",
    suta_rate_pct: "", suta_wage_cap: "", futa_rate_pct: "",
    futa_wage_cap: "", other_benefits_rate_pct: "",
  });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listBurdenRates());
      setError(null);
    } catch {
      setError("Failed to load compensation burden rates.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const openEdit = (row: CompensationBurdenRate): void => {
    setEditing(row);
    setForm({
      fica_rate_pct: row.fica_rate_pct,
      fica_wage_cap: row.fica_wage_cap,
      medicare_rate_pct: row.medicare_rate_pct,
      state_income_tax_rate_pct: row.state_income_tax_rate_pct,
      federal_income_tax_rate_pct: row.federal_income_tax_rate_pct,
      suta_rate_pct: row.suta_rate_pct,
      suta_wage_cap: row.suta_wage_cap,
      futa_rate_pct: row.futa_rate_pct,
      futa_wage_cap: row.futa_wage_cap,
      other_benefits_rate_pct: row.other_benefits_rate_pct,
    });
    setSaveError(null);
  };

  const handleSave = async (): Promise<void> => {
    if (!editing) return;
    setSaving(true);
    setSaveError(null);
    try {
      await upsertBurdenRate(editing.fiscal_year, form);
      setEditing(null);
      await load();
    } catch (err) {
      setSaveError(err instanceof ApiError ? String(err.detail) : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const columns: GridColDef[] = [
    { field: "fiscal_year", headerName: "Fiscal Year", width: 120 },
    {
      field: "fica_rate_pct",
      headerName: "FICA Rate",
      width: 110,
      renderCell: (p: GridRenderCellParams) => formatRate(String(p.value)),
    },
    {
      field: "fica_wage_cap",
      headerName: "FICA Cap",
      width: 120,
      renderCell: (p: GridRenderCellParams) => formatCurrency(String(p.value)),
    },
    {
      field: "medicare_rate_pct",
      headerName: "Medicare Rate",
      width: 130,
      renderCell: (p: GridRenderCellParams) => formatRate(String(p.value)),
    },
    {
      field: "suta_rate_pct",
      headerName: "SUTA Rate",
      width: 110,
      renderCell: (p: GridRenderCellParams) => formatRate(String(p.value)),
    },
    { field: "updated_by", headerName: "Last Updated By", flex: 1 },
    ...(writeAllowed
      ? [{
          field: "_actions",
          headerName: "",
          width: 80,
          sortable: false,
          renderCell: (p: GridRenderCellParams<CompensationBurdenRate>) => (
            <Button size="small" onClick={() => openEdit(p.row as CompensationBurdenRate)}>Edit</Button>
          ),
        }]
      : []),
  ];

  type BurdenField = keyof CompensationBurdenRateUpsert;
  const rateFields: { field: BurdenField; label: string }[] = [
    { field: "fica_rate_pct", label: "FICA Rate (%)" },
    { field: "fica_wage_cap", label: "FICA Wage Cap ($)" },
    { field: "medicare_rate_pct", label: "Medicare Rate (%)" },
    { field: "state_income_tax_rate_pct", label: "State Income Tax Rate (%)" },
    { field: "federal_income_tax_rate_pct", label: "Federal Income Tax Rate (%)" },
    { field: "suta_rate_pct", label: "SUTA Rate (%)" },
    { field: "suta_wage_cap", label: "SUTA Wage Cap ($)" },
    { field: "futa_rate_pct", label: "FUTA Rate (%)" },
    { field: "futa_wage_cap", label: "FUTA Wage Cap ($)" },
    { field: "other_benefits_rate_pct", label: "Other Benefits Rate (%)" },
  ];

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <DataGrid
        rows={rows}
        columns={columns}
        getRowId={(r: CompensationBurdenRate) => r.fiscal_year}
        loading={loading}
        autoHeight
        pageSizeOptions={[10, 25]}
        initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
      />
      <Dialog open={Boolean(editing)} onClose={() => setEditing(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Edit Compensation Burden Rates — FY {editing?.fiscal_year}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {saveError && <Alert severity="error">{saveError}</Alert>}
            {rateFields.map(({ field, label }) => (
              <TextField
                key={field}
                label={label}
                value={form[field]}
                onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                fullWidth
              />
            ))}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button onClick={() => { void handleSave(); }} variant="contained" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

// ===== Tab 3 — Compensation Component Mappings ==============================

function ComponentMappingsTab({ writeAllowed }: { writeAllowed: boolean }): JSX.Element {
  const [rows, setRows] = useState<CompensationComponentMapping[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<CompensationComponentMapping | null>(null);
  const [accountCode, setAccountCode] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listComponentMappings());
      setError(null);
    } catch {
      setError("Failed to load compensation component mappings.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const openEdit = (row: CompensationComponentMapping): void => {
    setEditing(row);
    setAccountCode(row.account_code);
    setSaveError(null);
  };

  const handleSave = async (): Promise<void> => {
    if (!editing) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateComponentMapping(editing.component_code, { account_code: accountCode });
      setEditing(null);
      await load();
    } catch (err) {
      setSaveError(err instanceof ApiError ? String(err.detail) : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const columns: GridColDef[] = [
    { field: "component_code", headerName: "Component Code", width: 200 },
    { field: "account_code", headerName: "Account Code", width: 140 },
    { field: "updated_by", headerName: "Last Updated By", flex: 1 },
    ...(writeAllowed
      ? [{
          field: "_actions",
          headerName: "",
          width: 80,
          sortable: false,
          renderCell: (p: GridRenderCellParams<CompensationComponentMapping>) => (
            <Button size="small" onClick={() => openEdit(p.row as CompensationComponentMapping)}>Edit</Button>
          ),
        }]
      : []),
  ];

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      <DataGrid
        rows={rows}
        columns={columns}
        getRowId={(r: CompensationComponentMapping) => r.component_code}
        loading={loading}
        autoHeight
        pageSizeOptions={[10, 25]}
        initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
      />
      <Dialog open={Boolean(editing)} onClose={() => setEditing(null)} maxWidth="xs" fullWidth>
        <DialogTitle>Edit Component Mapping</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {saveError && <Alert severity="error">{saveError}</Alert>}
            <TextField
              label="Component Code"
              value={editing?.component_code ?? ""}
              InputProps={{ readOnly: true }}
              fullWidth
            />
            <TextField
              label="Account Code"
              value={accountCode}
              onChange={(e) => setAccountCode(e.target.value)}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
          <Button onClick={() => { void handleSave(); }} variant="contained" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

// ===== Tab 4 — Overhead Allocation Rates ====================================

type OverheadForm = Omit<OverheadAllocationRateCreate, "geography_code"> & { geography_code: string };

const EMPTY_OVERHEAD_FORM: OverheadForm = {
  account_code: "",
  geography_code: "",
  amount_per_employee: "",
  rate_period: "Monthly",
  fiscal_year: new Date().getFullYear(),
};

function OverheadRatesTab({ writeAllowed }: { writeAllowed: boolean }): JSX.Element {
  const [rows, setRows] = useState<OverheadAllocationRate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogMode, setDialogMode] = useState<"create" | "edit" | null>(null);
  const [editing, setEditing] = useState<OverheadAllocationRate | null>(null);
  const [form, setForm] = useState<OverheadForm>(EMPTY_OVERHEAD_FORM);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listOverheadRates());
      setError(null);
    } catch {
      setError("Failed to load overhead allocation rates.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const openCreate = (): void => {
    setEditing(null);
    setForm(EMPTY_OVERHEAD_FORM);
    setSaveError(null);
    setDialogMode("create");
  };

  const openEdit = (row: OverheadAllocationRate): void => {
    setEditing(row);
    setForm({
      account_code: row.account_code,
      geography_code: row.geography_code ?? "",
      amount_per_employee: row.amount_per_employee,
      rate_period: row.rate_period,
      fiscal_year: row.fiscal_year,
    });
    setSaveError(null);
    setDialogMode("edit");
  };

  const handleDeactivate = async (row: OverheadAllocationRate): Promise<void> => {
    try {
      await deactivateOverheadRate(row.rate_id);
      await load();
    } catch {
      setError("Failed to deactivate rate.");
    }
  };

  const handleSave = async (): Promise<void> => {
    setSaving(true);
    setSaveError(null);
    try {
      if (dialogMode === "create") {
        await createOverheadRate({
          ...form,
          geography_code: form.geography_code || null,
        });
      } else if (editing) {
        await updateOverheadRate(editing.rate_id, {
          amount_per_employee: form.amount_per_employee,
          rate_period: form.rate_period,
        });
      }
      setDialogMode(null);
      await load();
    } catch (err) {
      setSaveError(err instanceof ApiError ? String(err.detail) : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const columns: GridColDef[] = [
    { field: "fiscal_year", headerName: "Fiscal Year", width: 120 },
    { field: "account_code", headerName: "Account Code", width: 130 },
    { field: "geography_code", headerName: "Geography", width: 110 },
    {
      field: "amount_per_employee",
      headerName: "Amount / Employee",
      width: 160,
      renderCell: (p: GridRenderCellParams) => formatCurrency(String(p.value)),
    },
    { field: "rate_period", headerName: "Period", width: 100 },
    {
      field: "is_active",
      headerName: "Active",
      width: 90,
      renderCell: (p: GridRenderCellParams) => (
        <Chip
          label={p.value ? "Active" : "Inactive"}
          color={p.value ? "success" : "default"}
          size="small"
          variant="outlined"
        />
      ),
    },
    ...(writeAllowed
      ? [{
          field: "_actions",
          headerName: "",
          width: 160,
          sortable: false,
          renderCell: (p: GridRenderCellParams<OverheadAllocationRate>) => {
            const row = p.row as OverheadAllocationRate;
            return (
              <Stack direction="row" spacing={0.5}>
                <Button size="small" onClick={() => openEdit(row)}>Edit</Button>
                {row.is_active && (
                  <Button
                    size="small"
                    color="warning"
                    onClick={() => { void handleDeactivate(row); }}
                  >
                    Deactivate
                  </Button>
                )}
              </Stack>
            );
          },
        }]
      : []),
  ];

  return (
    <Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {writeAllowed && (
        <Box sx={{ mb: 2 }}>
          <Button variant="contained" size="small" onClick={openCreate}>
            + Add Overhead Rate
          </Button>
        </Box>
      )}
      <DataGrid
        rows={rows}
        columns={columns}
        getRowId={(r: OverheadAllocationRate) => r.rate_id}
        loading={loading}
        autoHeight
        pageSizeOptions={[10, 25]}
        initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
      />
      <Dialog open={dialogMode !== null} onClose={() => setDialogMode(null)} maxWidth="xs" fullWidth>
        <DialogTitle>{dialogMode === "create" ? "Add Overhead Allocation Rate" : "Edit Overhead Allocation Rate"}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {saveError && <Alert severity="error">{saveError}</Alert>}
            <TextField
              label="Account Code"
              value={form.account_code}
              onChange={(e) => setForm((f) => ({ ...f, account_code: e.target.value }))}
              InputProps={{ readOnly: dialogMode === "edit" }}
              fullWidth
            />
            <TextField
              label="Geography Code (leave blank for universal)"
              value={form.geography_code}
              onChange={(e) => setForm((f) => ({ ...f, geography_code: e.target.value }))}
              InputProps={{ readOnly: dialogMode === "edit" }}
              fullWidth
            />
            <TextField
              label="Amount per Employee ($)"
              value={form.amount_per_employee}
              onChange={(e) => setForm((f) => ({ ...f, amount_per_employee: e.target.value }))}
              fullWidth
            />
            <TextField
              label="Rate Period"
              value={form.rate_period}
              onChange={(e) => setForm((f) => ({ ...f, rate_period: e.target.value as "Monthly" | "Annual" }))}
              select
              SelectProps={{ native: true }}
              InputProps={{ readOnly: dialogMode === "edit" }}
              fullWidth
            >
              <option value="Monthly">Monthly</option>
              <option value="Annual">Annual</option>
            </TextField>
            {dialogMode === "create" && (
              <TextField
                label="Fiscal Year"
                type="number"
                value={form.fiscal_year}
                onChange={(e) => setForm((f) => ({ ...f, fiscal_year: parseInt(e.target.value, 10) }))}
                fullWidth
              />
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogMode(null)}>Cancel</Button>
          <Button onClick={() => { void handleSave(); }} variant="contained" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

// ===== Main exported component ==============================================

export function BusinessRulesConfig(): JSX.Element {
  const { user } = useAuth();
  const [tab, setTab] = useState(0);
  const write = canWrite(user);

  if (!user) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      <Typography variant="h5" gutterBottom>
        Business Rules Configuration
      </Typography>
      {!write && (
        <Alert severity="info" sx={{ mb: 2 }}>
          You have read-only access. Contact a System Modeler or Administrator to make changes.
        </Alert>
      )}
      <Box sx={{ borderBottom: 1, borderColor: "divider", mb: 2 }}>
        <Tabs value={tab} onChange={(_, v: number) => setTab(v)}>
          <Tab label="Merit Increase Rates" />
          <Tab label="Compensation Burden Rates" />
          <Tab label="Component Mappings" />
          <Tab label="Overhead Allocation Rates" />
        </Tabs>
      </Box>
      {tab === 0 && <MeritRatesTab writeAllowed={write} />}
      {tab === 1 && <BurdenRatesTab writeAllowed={write} />}
      {tab === 2 && <ComponentMappingsTab writeAllowed={write} />}
      {tab === 3 && <OverheadRatesTab writeAllowed={write} />}
    </Box>
  );
}
