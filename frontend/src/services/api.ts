import axios from "axios";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
  },
});

const TOKEN_KEY = "access_token";
const TOKEN_EXPIRY_KEY = "token_expiry";
const SESSION_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes

export function getToken(): string | null {
  const token = localStorage.getItem(TOKEN_KEY);
  const expiry = localStorage.getItem(TOKEN_EXPIRY_KEY);

  if (!token || !expiry) {
    return null;
  }

  if (Date.now() > Number(expiry)) {
    clearToken();
    return null;
  }

  return token;
}

export function setToken(token: string, expiresIn: number): void {
  localStorage.setItem(TOKEN_KEY, token);
  const expiryTime = Date.now() + Math.min(expiresIn * 1000, SESSION_TIMEOUT_MS);
  localStorage.setItem(TOKEN_EXPIRY_KEY, String(expiryTime));
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(TOKEN_EXPIRY_KEY);
}

// Request interceptor to add auth header
api.interceptors.request.use(
  (config) => {
    const token = getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor to handle 401 (session expired)
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      clearToken();
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export default api;
