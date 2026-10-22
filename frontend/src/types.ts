export type Choice = { value: string; label: string };

export type FieldMeta = {
  name: string;
  label: string;
  type: "text" | "textarea" | "email" | "integer" | "decimal" | "boolean" | "date" | "datetime" | "time" | "json" | "choice" | "fk" | "computed"
    | "password" | "lookup" | "multiselect" | "checklist";
  readonly?: boolean;
  required?: boolean;
  choices?: Choice[];
  target?: string | null;
  help?: string;
  depends?: Record<string, string>;
  filter?: Record<string, string>;
  source?: string;
  decimals?: number;
};

export type ActionMeta = {
  name: string;
  label: string;
  fields: FieldMeta[];
  reason_required: boolean;
  confirm: string;
  tone: "default" | "primary" | "danger";
  detail: boolean;
};

export type ResourceMeta = {
  key: string;
  label: string;
  nav?: boolean;
  singular: string;
  module: string;
  title_field: string;
  status_field: string | null;
  list_fields: string[];
  form_fields: string[];
  create_only_fields: string[];
  fields: Record<string, FieldMeta>;
  filters: FieldMeta[];
  searchable: boolean;
  can_create: boolean;
  can_update: boolean;
  can_export: boolean;
  actions: ActionMeta[];
  inlines: { resource: string; fk: string; label: string }[];
  help: { why?: string; steps?: string; effect?: string; boundary?: string };
  approval_rules: ApprovalRule[];
};

export type ApprovalRule = { operation: "create" | "update" | "action"; action: string; condition: string; fields: string[];
  amount_threshold: string | null; approvals: number; name: string };

export type ModuleMeta = { key: string; label: string; resources: ResourceMeta[] };

export type Profile = {
  id: string;
  username: string;
  full_name: string;
  department: string | null;
  job_title: string;
  roles: string[];
  levels: string[];
  permissions: string[];
  mfa_enabled: boolean;
  must_change_password: boolean;
  ui_preferences?: Record<string, string>;
  current_shift: { date: string; shift: string; label: string };
  device?: { id: string; name: string } | null;
};

export type Row = Record<string, any> & { id: string; _meta?: { editable: boolean; locked_reason: string | null; actions: string[]; pending_approvals?: string[] }; _extra?: any };
