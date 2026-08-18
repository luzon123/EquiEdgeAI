/**
 * The entire production UI. On purpose, there is nothing else here:
 *
 *   - Before analysis: one button.
 *   - After analysis: a winrate and an action, in large text.
 *
 * The primary entry points on-device are the iOS Share Extension and the
 * Android floating overlay (neither renders this screen at all — they
 * call analyzeScreenshot() directly and show their own minimal result
 * bubble). This screen exists so the app is independently usable/testable
 * without either native shell wired up, and as a fallback manual flow.
 */
import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  SafeAreaView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { launchImageLibrary } from 'react-native-image-picker';
import { analyzeScreenshot, ImageSource } from '../api/client';
import { AnalyzeResult, isAnalyzeError } from '../types/result';
import { actionColor, colors } from '../theme/colors';

type Status = 'idle' | 'analyzing' | 'done';

export default function HomeScreen(): JSX.Element {
  const [status, setStatus] = useState<Status>('idle');
  const [result, setResult] = useState<AnalyzeResult | null>(null);

  const pickAndAnalyze = useCallback(async () => {
    const picked = await launchImageLibrary({ mediaType: 'photo', selectionLimit: 1 });
    const asset = picked.assets?.[0];
    if (!asset?.uri) {
      return; // user cancelled — stay idle, nothing to report
    }

    const image: ImageSource = {
      uri: asset.uri,
      name: asset.fileName ?? 'screenshot.jpg',
      type: asset.type ?? 'image/jpeg',
    };

    setStatus('analyzing');
    const outcome = await analyzeScreenshot(image);
    setResult(outcome);
    setStatus('done');
  }, []);

  const reset = useCallback(() => {
    setResult(null);
    setStatus('idle');
  }, []);

  return (
    <SafeAreaView style={styles.root}>
      <View style={styles.center}>
        {status === 'idle' && (
          <Pressable style={styles.primaryButton} onPress={pickAndAnalyze}>
            <Text style={styles.primaryButtonText}>Analyze Screenshot</Text>
          </Pressable>
        )}

        {status === 'analyzing' && (
          <View style={styles.centerColumn}>
            <ActivityIndicator size="large" color={colors.accent} />
            <Text style={styles.subtle}>Analyzing…</Text>
          </View>
        )}

        {status === 'done' && result && (
          <View style={styles.centerColumn}>
            {isAnalyzeError(result) ? (
              <>
                <Text style={styles.errorText}>{result.error}</Text>
              </>
            ) : (
              <>
                <Text style={styles.winrate}>{Math.round(result.winrate)}%</Text>
                <Text style={[styles.action, { color: actionColor(result.action) }]}>
                  {result.action}
                </Text>
              </>
            )}
            <Pressable style={styles.secondaryButton} onPress={reset}>
              <Text style={styles.secondaryButtonText}>Analyze Another</Text>
            </Pressable>
          </View>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 24 },
  centerColumn: { alignItems: 'center', gap: 16 },
  primaryButton: {
    backgroundColor: colors.accent,
    paddingVertical: 18,
    paddingHorizontal: 36,
    borderRadius: 16,
  },
  primaryButtonText: { color: '#04140C', fontSize: 18, fontWeight: '700' },
  secondaryButton: { marginTop: 8, paddingVertical: 10, paddingHorizontal: 20 },
  secondaryButtonText: { color: colors.textMuted, fontSize: 15 },
  subtle: { color: colors.textMuted, fontSize: 15 },
  winrate: { color: colors.text, fontSize: 72, fontWeight: '800' },
  action: { fontSize: 40, fontWeight: '800', letterSpacing: 1 },
  errorText: { color: colors.textMuted, fontSize: 17, textAlign: 'center', maxWidth: 280 },
});
