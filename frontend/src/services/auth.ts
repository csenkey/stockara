import api, { setToken, clearToken } from "./api";

export interface RegisterRequest {
  email: string;
  password: string;
}

export interface RegisterResponse {
  message: string;
  user_id: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export async function register(data: RegisterRequest): Promise<RegisterResponse> {
  const response = await api.post<RegisterResponse>("/api/auth/register", data);
  return response.data;
}

export async function login(data: LoginRequest): Promise<LoginResponse> {
  const response = await api.post<LoginResponse>("/api/auth/login", data);
  setToken(response.data.access_token, response.data.expires_in);
  return response.data;
}

export function logout(): void {
  clearToken();
  window.location.href = "/login";
}
