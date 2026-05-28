import { useState, FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { login, register } from "../services/auth";
import { AxiosError } from "axios";

type AuthMode = "login" | "register";

interface ValidationErrors {
  email?: string;
  password?: string;
}

const PASSWORD_MIN_LENGTH = 8;
const PASSWORD_MAX_LENGTH = 128;

function validateEmail(email: string): string | undefined {
  if (!email) return "Email is required";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return "Please enter a valid email address";
  return undefined;
}

function validatePassword(password: string): string | undefined {
  if (!password) return "Password is required";
  if (password.length < PASSWORD_MIN_LENGTH) return `Password must be at least ${PASSWORD_MIN_LENGTH} characters`;
  if (password.length > PASSWORD_MAX_LENGTH) return `Password must be at most ${PASSWORD_MAX_LENGTH} characters`;
  if (!/[A-Z]/.test(password)) return "Password must contain at least one uppercase letter";
  if (!/[a-z]/.test(password)) return "Password must contain at least one lowercase letter";
  if (!/\d/.test(password)) return "Password must contain at least one digit";
  return undefined;
}

export default function Login() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [serverError, setServerError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  function switchMode(newMode: AuthMode) {
    setMode(newMode);
    setErrors({});
    setServerError("");
    setSuccessMessage("");
  }

  function validate(): boolean {
    const newErrors: ValidationErrors = {};
    newErrors.email = validateEmail(email);
    newErrors.password = validatePassword(password);

    const hasErrors = Object.values(newErrors).some(Boolean);
    setErrors(hasErrors ? newErrors : {});
    return !hasErrors;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setServerError("");
    setSuccessMessage("");

    if (!validate()) return;

    setIsLoading(true);
    try {
      if (mode === "register") {
        await register({ email, password });
        setSuccessMessage("Registration successful! You can now log in.");
        setMode("login");
        setPassword("");
      } else {
        await login({ email, password });
        navigate("/dashboard");
      }
    } catch (err) {
      const axiosError = err as AxiosError<{ detail?: string }>;
      const status = axiosError.response?.status;

      if (status === 429) {
        setServerError("Account temporarily locked. Please try again later.");
      } else if (mode === "login") {
        setServerError("Invalid credentials.");
      } else {
        const detail = axiosError.response?.data?.detail;
        setServerError(detail || "Registration failed. Please try again.");
      }
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-md">
        <div className="bg-white rounded-lg shadow-md p-8">
          <h1 className="text-2xl font-bold text-center text-gray-900 mb-6">
            Stock Monitor
          </h1>

          {/* Tabs */}
          <div className="flex border-b border-gray-200 mb-6" role="tablist">
            <button
              role="tab"
              aria-selected={mode === "login"}
              className={`flex-1 py-2 text-center font-medium border-b-2 transition-colors ${
                mode === "login"
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
              onClick={() => switchMode("login")}
            >
              Login
            </button>
            <button
              role="tab"
              aria-selected={mode === "register"}
              className={`flex-1 py-2 text-center font-medium border-b-2 transition-colors ${
                mode === "register"
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-gray-500 hover:text-gray-700"
              }`}
              onClick={() => switchMode("register")}
            >
              Register
            </button>
          </div>

          {/* Messages */}
          {serverError && (
            <div
              className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm"
              role="alert"
            >
              {serverError}
            </div>
          )}
          {successMessage && (
            <div
              className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm"
              role="alert"
            >
              {successMessage}
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} noValidate>
            <div className="mb-4">
              <label
                htmlFor="email"
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                Email
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                  errors.email ? "border-red-500" : "border-gray-300"
                }`}
                placeholder="you@example.com"
              />
              {errors.email && (
                <p className="mt-1 text-sm text-red-600">{errors.email}</p>
              )}
            </div>

            <div className="mb-6">
              <label
                htmlFor="password"
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                  errors.password ? "border-red-500" : "border-gray-300"
                }`}
                placeholder="••••••••"
              />
              {errors.password && (
                <p className="mt-1 text-sm text-red-600">{errors.password}</p>
              )}
              {mode === "register" && !errors.password && (
                <p className="mt-1 text-xs text-gray-500">
                  8-128 characters, at least one uppercase, one lowercase, and one digit
                </p>
              )}
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full py-2 px-4 bg-blue-600 text-white font-medium rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isLoading
                ? "Please wait..."
                : mode === "login"
                ? "Sign In"
                : "Create Account"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
