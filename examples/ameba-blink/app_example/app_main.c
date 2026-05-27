/*
 * app_example/app_main.c
 *
 * Required by the Ameba SDK: defines `void app_example(void)`, which the
 * SDK's main() calls during system bring-up. Treat this as the equivalent
 * of `app_main()` in ESP-IDF.
 *
 * For the Blink example we delegate to user_main() which lives in src/
 * (PIO-standard location). That keeps editor focus on src/ where the
 * IDE expects user code.
 */

extern void user_main(void);

void app_example(void)
{
    user_main();
}
