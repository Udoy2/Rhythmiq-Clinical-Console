/*
 * CardioSense AI - AD8232 single-lead ECG front-end firmware (Arduino Uno / Nano, ATmega328P @ 16 MHz)
 *
 * Wiring (SparkFun AD8232 breakout):
 *   AD8232 3.3V  -> Arduino 3.3V        AD8232 GND -> Arduino GND
 *   AD8232 OUTPUT-> A0                  AD8232 LO+ -> D10        AD8232 LO- -> D11
 *   Electrodes: RA / LA / RL (lead II placement). Power from battery or a laptop on battery - never a
 *   mains-connected supply while electrodes are on a person.
 *
 * Output: 115200 baud, text, one line per sample at exactly 250 Hz (Timer1 compare interrupt, not delay()):
 *   "<adc>\n"   10-bit raw ADC value 0..1023 (no filtering - the server filters, identical to training)
 *   "!\n"       electrodes off (LO+ or LO- high) for this sample slot
 *   "# ...\n"   firmware messages. On connect (the board resets when the port opens) and whenever the host
 *               sends '?':   # cardiosense-ad8232 fw=1.0.0 fs=250 adc_bits=10
 *               If the main loop ever misses sample slots:   # overrun=<total missed>
 * Missing samples are never interpolated; sender/serial_bridge.py turns the line stream into frames.
 */

#define FW_VERSION "1.0.0"

const uint16_t SAMPLE_HZ   = 250;
const uint8_t  PIN_ECG     = A0;
const uint8_t  PIN_LO_PLUS = 10;
const uint8_t  PIN_LO_MINUS = 11;

volatile uint16_t g_sample;
volatile bool     g_leadOff;
volatile bool     g_ready;
volatile uint16_t g_overruns;

// Fires every 4 ms. Sampling here (not in loop()) keeps the sample instants fixed even while loop() prints.
ISR(TIMER1_COMPA_vect) {
  if (g_ready) g_overruns++;                       // previous sample not sent yet -> it is lost, and counted
  g_leadOff = digitalRead(PIN_LO_PLUS) || digitalRead(PIN_LO_MINUS);
  g_sample  = analogRead(PIN_ECG);                 // ~0.1 ms conversion
  g_ready   = true;
}

void printBanner() {
  Serial.print(F("# cardiosense-ad8232 fw=" FW_VERSION " fs="));
  Serial.print(SAMPLE_HZ);
  Serial.println(F(" adc_bits=10"));
}

void setup() {
  pinMode(PIN_LO_PLUS, INPUT);
  pinMode(PIN_LO_MINUS, INPUT);
  Serial.begin(115200);
  printBanner();

  noInterrupts();                                  // Timer1, CTC mode: 16 MHz / 64 / 1000 = 250 Hz exactly
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11) | _BV(CS10);     // CTC, prescaler 64
  OCR1A  = (uint16_t)(F_CPU / 64UL / SAMPLE_HZ - 1);
  TCNT1  = 0;
  TIMSK1 = _BV(OCIE1A);
  interrupts();
}

void loop() {
  static uint16_t reportedOverruns = 0;

  if (g_ready) {
    noInterrupts();
    uint16_t sample = g_sample;
    bool leadOff = g_leadOff;
    uint16_t overruns = g_overruns;
    g_ready = false;
    interrupts();

    if (leadOff) Serial.println('!');
    else         Serial.println(sample);

    if (overruns != reportedOverruns) {
      reportedOverruns = overruns;
      Serial.print(F("# overrun="));
      Serial.println(overruns);
    }
  }

  if (Serial.available() && Serial.read() == '?') printBanner();
}
