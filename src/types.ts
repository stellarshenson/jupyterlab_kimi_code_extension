export interface ISession {
  project_path: string;
  encoded_path: string;
  /** Full prefixed conversation id (`session_<uuid>`), as Kimi names the
   * session directory. */
  session_id: string;
  name: string;
  name_source: 'session' | 'basename';
  message_count: number;
  file_mtime: number;
  git_branch: string | null;
  favourite: boolean;
  extra_sessions: number;
}

export interface ISessionsListResponse {
  sessions: ISession[];
}

export interface IStatusResponse {
  enabled: boolean;
  kimi_path: string | null;
  root_dir: string;
}

export interface IFavouriteRequest {
  project_path: string;
  favourite: boolean;
}

export interface IFavouriteResponse {
  favourites: string[];
}

export interface IRemoveRequest {
  encoded_path: string;
}

export interface IRemoveResponse {
  removed: string;
}

export interface ICleanupRequest {
  encoded_path: string;
}

export interface ICleanupResponse {
  removed_count: number;
}

export interface IBranch {
  session_id: string;
  file_mtime: number;
  label: string;
}

export interface IBranchesResponse {
  current: string;
  branches: IBranch[];
}

export interface ISwitchRequest {
  encoded_path: string;
  session_id: string;
}

export interface ISwitchResponse {
  requested: string;
  current: string | null;
}

export interface IDeleteBranchesRequest {
  encoded_path: string;
  session_ids: string[];
}

export interface IDeleteBranchesResponse {
  removed_count: number;
}

export interface IForkRequest {
  encoded_path: string;
  session_id: string;
  name?: string;
}

export interface IForkResponse {
  /** Full prefixed id (`session_<uuid>`) of the freshly copied conversation. */
  session_id: string;
  forked_from: string;
}

export interface ILaunchTerminalRequest {
  project_path: string;
  /** The conversation to resume (`kimi -S <session_id>`). Omit to start a
   * brand-new Kimi session instead - Kimi assigns the id itself, so a new
   * session carries no pre-assigned id. */
  session_id?: string;
  /** Append `--yolo` to the launched argv (Kimi auto-approves tool calls). */
  yolo?: boolean;
}

export interface ILaunchTerminalResponse {
  terminal_name: string;
}

export interface ITerminalCwdResponse {
  terminal_name: string;
  cwds: string[];
  has_kimi: boolean;
  /** Conversation id the running kimi is resuming (full `session_<uuid>`),
   * or null when it cannot be read from the argv (bare `kimi`, `-c`). Lets
   * reuse tell branches of one project apart. */
  session_id?: string | null;
}
