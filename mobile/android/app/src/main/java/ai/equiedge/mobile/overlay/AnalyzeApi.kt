package ai.equiedge.mobile.overlay

import ai.equiedge.mobile.BuildConfig
import android.util.Log
import java.io.OutputStream
import java.util.UUID
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

private const val TAG = "EquiEdgeAnalyze"

/**
 * Same server contract as mobile/src/api/client.ts (React Native) and
 * mobile/ios-share-extension/.../ShareViewController.swift (iOS). All
 * three platforms upload a screenshot to the same POST /mobile/analyze
 * and expect the same {"winrate": number, "action": string} |
 * {"error": string} response shape. Keep them in sync.
 *
 * Uses plain HttpURLConnection rather than a networking library (OkHttp,
 * Retrofit, ...) deliberately — this module ships as source to be dropped
 * into the host app's Gradle project, and a hand-authored source drop
 * should not silently require dependencies the host project may not have
 * declared yet.
 *
 * Base URL is NOT hardcoded here: it comes from BuildConfig.API_BASE_URL,
 * set per build type in android/app/build.gradle — debug builds point at
 * 127.0.0.1 via `adb reverse tcp:5000 tcp:5000` for physical-device
 * development (see build.gradle's comment for the emulator/LAN
 * alternatives), release builds point at production.
 */
object AnalyzeApi {

    private val ENDPOINT = "${BuildConfig.API_BASE_URL}/mobile/analyze"
    private const val TIMEOUT_MS = 12_000

    sealed class Result {
        data class Success(val winrate: Double, val action: String) : Result()
        data class Failure(val message: String) : Result()
    }

    /** Blocking call — invoke from a background thread/coroutine, never the main thread. */
    fun analyze(imageBytes: ByteArray): Result {
        val boundary = "EquiEdgeBoundary-${UUID.randomUUID()}"
        val isPng = imageBytes.size >= 4 &&
            imageBytes[0] == 0x89.toByte() && imageBytes[1] == 0x50.toByte() &&
            imageBytes[2] == 0x4E.toByte() && imageBytes[3] == 0x47.toByte()
        val filename = if (isPng) "table.png" else "table.jpg"
        val contentType = if (isPng) "image/png" else "image/jpeg"

        Log.d(TAG, "[ANALYZE] preparing HTTP request, ${imageBytes.size} bytes")
        Log.d(TAG, "[ANALYZE] API URL: $ENDPOINT")

        var connection: HttpURLConnection? = null
        val startedAt = System.currentTimeMillis()
        try {
            connection = (URL(ENDPOINT).openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                connectTimeout = TIMEOUT_MS
                readTimeout = TIMEOUT_MS
                setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
            }

            Log.d(TAG, "[ANALYZE] HTTP request started")
            connection.outputStream.use { out: OutputStream ->
                out.write("--$boundary\r\n".toByteArray())
                out.write(
                    "Content-Disposition: form-data; name=\"image\"; filename=\"$filename\"\r\n"
                        .toByteArray()
                )
                out.write("Content-Type: $contentType\r\n\r\n".toByteArray())
                out.write(imageBytes)
                out.write("\r\n--$boundary--\r\n".toByteArray())
            }

            val status = connection.responseCode
            val elapsedMs = System.currentTimeMillis() - startedAt
            Log.d(TAG, "[ANALYZE] HTTP response: $status (${elapsedMs}ms)")

            val body = (if (status in 200..299) connection.inputStream else connection.errorStream)
                ?.bufferedReader()?.use { it.readText() }
            if (body == null) {
                Log.e(TAG, "[ANALYZE][ERROR] response had no body (status=$status)")
                return Result.Failure("Analysis failed. Try again.")
            }
            Log.d(TAG, "[ANALYZE] response body received, ${body.length} chars")

            Log.d(TAG, "[ANALYZE] parsing result")
            val json = try {
                JSONObject(body)
            } catch (e: Exception) {
                Log.e(TAG, "[ANALYZE][ERROR] response body was not valid JSON: ${e.javaClass.simpleName}: ${e.message}")
                return Result.Failure("Analysis failed. Try again.")
            }

            if (status !in 200..299) {
                val message = json.optString("error", "Analysis failed. Try again.")
                Log.w(TAG, "[ANALYZE] server returned error status=$status message=$message")
                return Result.Failure(message)
            }

            if (!json.has("winrate") || !json.has("action")) {
                Log.e(TAG, "[ANALYZE][ERROR] response missing winrate/action fields: $body")
                return Result.Failure("Analysis failed. Try again.")
            }
            return Result.Success(json.getDouble("winrate"), json.getString("action"))
        } catch (e: java.net.SocketTimeoutException) {
            Log.e(TAG, "[ANALYZE][ERROR] timed out after ${System.currentTimeMillis() - startedAt}ms: ${e.message}")
            return Result.Failure("Analysis timed out. Try again.")
        } catch (e: java.io.IOException) {
            Log.e(TAG, "[ANALYZE][ERROR] ${e.javaClass.simpleName}: ${e.message}")
            return Result.Failure("Could not reach the server. Check your connection and try again.")
        } finally {
            connection?.disconnect()
        }
    }
}
